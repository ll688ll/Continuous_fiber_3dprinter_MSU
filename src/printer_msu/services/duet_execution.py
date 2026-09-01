"""Duet job execution strategies (V2-M3).

The Duet runs whole programs; per-move dispatch would throw away the
look-ahead planner that makes it fast. Two modes, both required (D7):

* ``DuetFileExecutor`` — **preload, the default**: upload the emitted program
  to the board's SD (CRC-verified), start it with M32, follow progress from
  the board's own object model. Nothing in the host path can disturb the
  print.
* ``DuetStreamExecutor`` — **stream**: feed the same program through
  ``rr_gcode``, batched BY BYTES against the live buffer (the board silently
  drops overflow). For host-in-the-loop work; measured +1.2 % vs preload.

Hard-won rules encoded here (lab 2026-08-25):
* verify what is actually running — check the M32 reply AND
  ``job.file.fileName`` before trusting progress (a swallowed reply once
  produced five minutes of numbers from the PREVIOUS job);
* read durations from the board (``job.duration``/``lastDuration``), never a
  host timer (file mode; stream mode has no board-side job, its duration is
  host-measured and labelled so);
* progress cannot be finer than RRF's 60-move look-ahead — events lead the
  physical machine.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Optional

from ..core.gcode.emitter import EmittedProgram, emit_machine_gcode
from ..errors import PrinterError

if TYPE_CHECKING:  # avoid a circular import; the runner constructs us
    from .print_service import PrintJobRunner

logger = logging.getLogger("printer_msu.duet_execution")


class _Cancelled(Exception):
    """Internal: the job observed a stop request (runner maps to STOPPED)."""


class _DuetExecutorBase:
    POLL_S = 1.0
    IDLE_SETTLE = 3

    label = "?"

    def __init__(self, runner: "PrintJobRunner") -> None:
        self.runner = runner
        self.adapter = runner.adapter
        self.client = runner.adapter.client  # DuetMotionAdapter exposes it

    # ------------------------------------------------------------ shared bits
    def _emit_progress(self, index: int, total: int, line_no: int,
                       percent: float) -> None:
        from .print_service import JobEvent

        if line_no:
            self.runner.last_line = line_no
        self.runner._emit(JobEvent(
            kind="progress", job_id=self.runner.job_id,
            action_index=index, action_total=total,
            line_no=line_no, percent=min(100.0, percent)))

    def _check_stop(self) -> None:
        if self.runner._stop_requested:
            raise _Cancelled()

    def _hold_if_paused(self, on_pause, on_resume) -> None:
        """Mirror the runner's pause protocol; backend hooks do the M-codes."""
        from ..core.model.print_job import PrintJobState

        if self.runner._unpaused.is_set():
            return
        on_pause()
        self.runner._set_state(PrintJobState.PAUSED)
        self.runner._unpaused.wait()
        self._check_stop()
        on_resume()
        self.runner._set_state(PrintJobState.RUNNING)

    def _wait_idle_settled(self, extra_done=None,
                           timeout_s: float = 3600.0) -> None:
        idle_reads = 0
        deadline = time.monotonic() + timeout_s
        while idle_reads < self.IDLE_SETTLE:
            self._check_stop()
            status = str(self.client.model("state.status") or "unknown")
            if status == "halted":
                raise PrinterError("board halted (M112/fault) during the job")
            done = status == "idle" and (extra_done is None or extra_done())
            idle_reads = idle_reads + 1 if done else 0
            if time.monotonic() > deadline:
                raise PrinterError("job did not finish before the watchdog")
            if idle_reads < self.IDLE_SETTLE:
                time.sleep(self.POLL_S)

    def _verify_final_position(self, program: EmittedProgram) -> None:
        if not program.final_targets:
            return
        positions = self.adapter.get_status().positions
        tolerance = self.runner.POSITION_TOLERANCE_MM
        mismatched = {
            a: positions.get(a, float("nan"))
            for a, target in program.final_targets.items()
            if abs(positions.get(a, float("nan")) - target) > tolerance}
        if mismatched:
            detail = ", ".join(
                f"{a} commanded {program.final_targets[a]:.3f} but at "
                f"{actual:.3f}" for a, actual in sorted(mismatched.items()))
            raise PrinterError(
                f"job reported complete but the machine is not at the final "
                f"targets ({detail})")

    def _emit_program(self) -> EmittedProgram:
        return emit_machine_gcode(self.runner._actions, self.runner.profile)

    # -------------------------------------------------------------- interface
    def run(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    def request_stop(self) -> None:
        """Called from runner.stop(); flags are already set. Backend-specific
        stop G-codes happen inside the worker loop (thread-safe: one client
        lock), so nothing to do here by default."""


class DuetFileExecutor(_DuetExecutorBase):
    """Mode B: upload + M32; the board owns the print."""

    label = "preload"
    START_VERIFY_S = 10.0

    def run(self) -> None:
        runner = self.runner
        program = self._emit_program()
        # rr_upload takes the volume-less path; M32 addresses volume 0:
        upload_path = f"/gcodes/msu_jobs/{runner.job_id}.g"
        board_path = f"0:{upload_path}"

        logger.info("[%s] uploading %d lines (%d bytes) to %s",
                    runner.job_id, len(program.lines), program.total_bytes,
                    upload_path)
        self.client.upload(upload_path, program.data())

        # start + VERIFY what is actually running
        if not self.client.wait_for_space(len(board_path) + 8):
            raise PrinterError("board buffer never freed for M32")
        self.client.send(f'M32 "{board_path}"')
        reply = self.client.reply()
        deadline = time.monotonic() + self.START_VERIFY_S
        running_name = None
        while time.monotonic() < deadline:
            running_name = self.client.model("job.file.fileName")
            if running_name and str(running_name).endswith(
                    f"{runner.job_id}.g"):
                break
            time.sleep(0.5)
        if not running_name or not str(running_name).endswith(
                f"{runner.job_id}.g"):
            raise PrinterError(
                f"M32 did not start our job (reply {reply!r}, running file "
                f"{running_name!r}) - refusing to report progress for an "
                f"unknown job")

        total = len(program.lines)
        board_duration = None
        paused_sent = False

        def send_pause() -> None:
            nonlocal paused_sent
            self.client.wait_for_space(8)
            self.client.send("M25")
            paused_sent = True

        def send_resume() -> None:
            nonlocal paused_sent
            self.client.wait_for_space(8)
            self.client.send("M24")
            paused_sent = False

        try:
            while True:
                if runner._stop_requested:
                    self._graceful_stop(paused_sent)
                    raise _Cancelled()
                self._hold_if_paused(send_pause, send_resume)
                status = str(self.client.model("state.status") or "unknown")
                if status == "halted":
                    raise PrinterError("board halted during the print")
                file_name = self.client.model("job.file.fileName")
                duration = self.client.model("job.duration")
                if duration is not None:
                    board_duration = float(duration)
                file_pos = self.client.model("job.filePosition")
                if file_pos is not None and program.total_bytes:
                    pos = int(file_pos)
                    percent = 100.0 * pos / program.total_bytes
                    self._emit_progress(
                        int(percent / 100.0 * total), total,
                        program.source_line_at_offset(pos), percent)
                if not file_name and status == "idle":
                    break
                time.sleep(self.POLL_S)
        except _Cancelled:
            raise
        # settle + prefer the board's final duration
        self._wait_idle_settled()
        last = self.client.model("job.lastDuration")
        if last:
            board_duration = float(last)
        runner.board_duration_s = board_duration
        runner.execution_label = self.label
        self._verify_final_position(program)
        self._emit_progress(total, total, runner.last_line, 100.0)

    def _graceful_stop(self, paused_sent: bool) -> None:
        """M25 (pause the file) then M0 (cancel); M112 is estop's job."""
        if self.runner._estop_requested:
            return  # adapter.estop() already sent M112; board is halted
        try:
            if not paused_sent:
                self.client.wait_for_space(8)
                self.client.send("M25")
            self.client.wait_for_space(8)
            self.client.send("M0")
        except Exception:
            logger.exception("graceful stop dispatch failed")


class DuetStreamExecutor(_DuetExecutorBase):
    """Mode A: feed the emitted program through rr_gcode, batched by bytes.

    Pause simply stops feeding (queued moves — up to RRF's 60-move look-ahead
    — still finish); stop stops feeding and lets the queue drain; estop is
    M112 via the adapter as everywhere else.
    """

    label = "stream"
    FEED_DEADLINE_S = 60.0
    MAX_STALLS = 5

    def run(self) -> None:
        from ..controllers.motion.duet.client import BUFF_TOTAL

        runner = self.runner
        program = self._emit_program()
        # comments cost buffer bytes and do nothing on-board
        feed_lines = [(line, src) for line, src in
                      zip(program.lines, program.source_lines)
                      if line.strip() and not line.strip().startswith(";")]
        total = len(feed_lines)
        started = time.monotonic()
        i = 0
        stalls = 0
        while i < total:
            if runner._stop_requested:
                raise _Cancelled()
            self._hold_if_paused(lambda: None, lambda: None)
            # pack a batch by BYTES against the live buffer (silent-drop trap)
            first_cost = len(feed_lines[i][0].encode("ascii", "replace")) + 1
            if not self.client.wait_for_space(first_cost,
                                              deadline_s=self.FEED_DEADLINE_S):
                stalls += 1
                if stalls > self.MAX_STALLS:
                    raise PrinterError("board buffer stalled; aborting stream")
                continue
            batch: list[str] = []
            used = 0
            j = i
            while j < total:
                cost = len(feed_lines[j][0].encode("ascii", "replace")) + 1
                if used + cost > min(BUFF_TOTAL - 1, self.client.avail):
                    break
                batch.append(feed_lines[j][0])
                used += cost
                j += 1
            self.client.send("\n".join(batch))
            i = j
            self._emit_progress(i, total, feed_lines[i - 1][1],
                                100.0 * i / total)
        self._wait_idle_settled()
        runner.execution_label = self.label
        # no board-side job object in stream mode; the duration is host time
        # and is labelled as such in the report
        runner.board_duration_s = None
        runner.host_duration_s = round(time.monotonic() - started, 1)
        self._verify_final_position(program)
        self._emit_progress(total, total, runner.last_line, 100.0)


def make_executor(runner: "PrintJobRunner", mode: str):
    if mode == "stream":
        return DuetStreamExecutor(runner)
    return DuetFileExecutor(runner)

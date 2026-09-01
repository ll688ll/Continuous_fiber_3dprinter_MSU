"""Print job execution: preflight, streaming worker, events, job report
(printer PRD FR-PRE-01..02, FR-JOB-01..07).

The worker thread executes the interpreted action stream sequentially and
blocking against the motion HAL; pause takes effect between actions
(hardware-safe — FR-LINE-04), stop/e-stop preempt the in-flight move through
the library. Observers receive ``JobEvent``s from the worker thread and must
be cheap (the future Qt bridge marshals them to signals).
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from ..config.profile import MachineProfile, POLICY_CLAMP, POLICY_ERROR
from ..controllers.motion.base_motion_controller import StopMode
from ..core.gcode.interpreter import InterpretResult
from ..core.model.actions import (
    Action,
    EndProgram,
    HomeAxes,
    MoveLinear,
    PauseProgram,
    SelectTool,
    SkippedCommand,
)
from ..core.model.print_job import PrintJobState, TERMINAL_STATES
from ..errors import JobError

logger = logging.getLogger("printer_msu.print")

_POSITION_EPSILON_MM = 1e-6


# ------------------------------------------------------------------ preflight
@dataclass(frozen=True)
class Finding:
    level: str          # "error" | "warning" | "info"
    message: str

    def __str__(self) -> str:
        return f"[{self.level}] {self.message}"


@dataclass
class PreflightReport:
    findings: list[Finding] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not any(f.level == "error" for f in self.findings)

    def add(self, level: str, message: str) -> None:
        self.findings.append(Finding(level, message))


def run_preflight(profile: MachineProfile, adapter,
                  result: InterpretResult, *,
                  home_first: bool = False,
                  offline: bool = False) -> PreflightReport:
    """FR-PRE-01 checks against the interpreted stream and the live controller.

    ``offline=True`` (used by `check` without a reachable controller) skips
    live-status checks and treats every axis as unhomed.
    """
    report = PreflightReport()

    for diag in result.errors:
        report.add("error", f"line {diag.line_no}: {diag.message}")
    for diag in result.warnings:
        report.add("warning", f"line {diag.line_no}: {diag.message}")

    if not offline:
        try:
            status = adapter.get_status()
        except Exception as exc:  # connection problems fail preflight, not crash
            report.add("error", f"controller not reachable: {exc}")
            return report
        if status.emergency_active:
            report.add("error", "emergency stop is active")

    moves = [a for a in result.actions if isinstance(a, MoveLinear)]

    # homing coverage (FR-PRE-01 / FR-JOB-07)
    will_home_first = home_first or profile.policies.home_before_print
    if profile.policies.require_homed and not will_home_first:
        homed = set() if offline else adapter.homed_axes()
        first_move: dict[str, int] = {}
        first_home: dict[str, int] = {}
        for i, action in enumerate(result.actions):
            if isinstance(action, MoveLinear):
                for axis in action.axes:
                    first_move.setdefault(axis, i)
            elif isinstance(action, HomeAxes):
                covered = action.axes if action.axes is not None \
                    else profile.axis_names
                for axis in covered:
                    first_home.setdefault(axis, i)
        for axis, move_idx in sorted(first_move.items()):
            if axis in homed:
                continue
            home_idx = first_home.get(axis)
            if home_idx is None or home_idx > move_idx:
                report.add(
                    "error",
                    f"axis {axis!r} is not homed and the file does not home it "
                    f"before first use (G28, --home-first, or "
                    f"policies.require_homed=false)")

    # composite feed vs. axis maxima (FR-PRE-01; policy: clamp or error)
    max_speeds = adapter.max_speeds()
    offending: list[tuple[int, float, float]] = []
    for move in moves:
        limit = min(max_speeds[a] for a in move.axes if a in max_speeds)
        if move.feed_mm_s > limit + 1e-9:
            offending.append((move.line_no, move.feed_mm_s, limit))
    if offending:
        line_no, feed, limit = offending[0]
        message = (f"{len(offending)} move(s) exceed axis max speed (first: "
                   f"line {line_no}, {feed:.1f} > {limit:.1f} mm/s)")
        if profile.policies.feed_over_max == POLICY_ERROR:
            report.add("error", message + " (policies.feed_over_max=error)")
        else:
            report.add("info", message + "; feeds will be clamped")

    if not moves and not any(isinstance(a, HomeAxes) for a in result.actions):
        report.add("warning", "file produces no motion at all")

    if not offline:
        _check_envelope(profile, adapter, result, report)

    return report


def _check_envelope(profile: MachineProfile, adapter,
                    result: InterpretResult, report: PreflightReport) -> None:
    """FR-PRE-03: walk the (machine-frame) move stream and flag targets
    outside the discovered soft limits, per axis, before anything moves."""
    limits = adapter.soft_limits()
    positions: dict[str, float] = {}
    violations: dict[str, list[tuple[int, float]]] = {}
    unchecked_relative = 0
    for action in result.actions:
        if isinstance(action, HomeAxes):
            covered = (action.axes if action.axes is not None
                       else profile.axis_names)
            for axis in covered:
                lo_hi = limits.get(axis)
                # approximation: homing parks near the low end (clearance)
                positions[axis] = lo_hi[0] if lo_hi else 0.0
        elif isinstance(action, MoveLinear):
            for axis, value in action.axes.items():
                if action.relative:
                    if axis not in positions:
                        unchecked_relative += 1
                        continue
                    positions[axis] += value
                else:
                    positions[axis] = value
                lo_hi = limits.get(axis)
                if lo_hi is None:
                    continue
                lo, hi = lo_hi
                target = positions[axis]
                if not (lo - 1e-6 <= target <= hi + 1e-6):
                    violations.setdefault(axis, []).append(
                        (action.line_no, target))
    for axis in sorted(limits):
        if limits[axis] is None and any(
                axis in a.axes for a in result.actions
                if isinstance(a, MoveLinear)):
            report.add("info",
                       f"axis {axis!r} has no discovered soft limits; "
                       f"envelope not checked (run self-check)")
    for axis, hits in sorted(violations.items()):
        lo, hi = limits[axis]  # type: ignore[misc]
        line_no, target = hits[0]
        report.add("error",
                   f"{len(hits)} move target(s) outside axis {axis!r} soft "
                   f"limits [{lo:.1f}, {hi:.1f}] mm — first at line {line_no} "
                   f"({axis}={target:.3f}); adjust workspace offsets or "
                   f"re-slice for this machine")
    if unchecked_relative:
        report.add("warning",
                   f"{unchecked_relative} relative move component(s) before "
                   f"any absolute position; envelope not checked for those")


# ------------------------------------------------------------------- events
@dataclass(frozen=True)
class JobEvent:
    kind: str                                   # state|progress|warning|error
    job_id: str
    state: Optional[PrintJobState] = None
    message: str = ""
    action_index: int = 0
    action_total: int = 0
    line_no: int = 0
    percent: float = 0.0


EventCallback = Callable[[JobEvent], None]


# -------------------------------------------------------------------- runner
class PrintJobRunner:
    """Owns one job's worker thread and state machine (FR-JOB-01..06)."""

    #: post-move position verification (lab 2026-08-12: the controller can
    #: accept Line_2Axis with rc=0 and silently not move — "stopped" alone
    #: cannot distinguish finished from never-started)
    POSITION_TOLERANCE_MM = 0.25

    def __init__(self, profile: MachineProfile, adapter,
                 result: InterpretResult, gcode_path: str | Path, *,
                 job_id: Optional[str] = None,
                 report_path: Optional[str | Path] = None,
                 home_first: bool = False) -> None:
        self.profile = profile
        self.adapter = adapter
        self.result = result
        self.gcode_path = Path(gcode_path)
        self.job_id = job_id or _dt.datetime.now().strftime("job-%Y%m%d-%H%M%S")
        self.report_path = Path(report_path) if report_path else None

        actions: list[Action] = [
            a for a in result.actions if not isinstance(a, SkippedCommand)
        ]
        if home_first or profile.policies.home_before_print:
            actions.insert(0, HomeAxes(line_no=0, axes=None))
        self._actions = actions

        self._state = PrintJobState.READY
        self._state_lock = threading.Lock()
        self._worker: Optional[threading.Thread] = None
        self._unpaused = threading.Event()
        self._unpaused.set()
        self._stop_requested = False
        self._estop_requested = False
        self._callbacks: list[EventCallback] = []
        self._warned_clamp = False
        self.last_line = 0
        self.error_message: Optional[str] = None
        self._started_at: Optional[str] = None
        self._finished_at: Optional[str] = None
        # streamed execution (FR-JOB-02): moves are dispatched ahead into the
        # library's per-axis queues so the controller's motion-start grace
        # overlaps real motion instead of adding dead time per segment.
        # Progress events may lead physical motion by <= stream_window moves.
        self.stream_window = max(1, profile.policies.stream_window)
        self._inflight: deque[tuple[object, int, dict]] = deque()
        self._inflight_axes: Optional[frozenset] = None
        self._predicted: Optional[dict[str, float]] = None
        self._last_targets: Optional[dict[str, float]] = None

    # ----------------------------------------------------------------- state
    @property
    def state(self) -> PrintJobState:
        return self._state

    def subscribe(self, callback: EventCallback) -> None:
        self._callbacks.append(callback)

    def _emit(self, event: JobEvent) -> None:
        for cb in self._callbacks:
            try:
                cb(event)
            except Exception:  # observer bugs must not kill the job
                logger.exception("job event callback raised")

    def _set_state(self, state: PrintJobState) -> None:
        with self._state_lock:
            if self._state in TERMINAL_STATES:
                return
            self._state = state
        logger.info("[%s] state -> %s", self.job_id, state.value)
        self._emit(JobEvent(kind="state", job_id=self.job_id, state=state))

    # --------------------------------------------------------------- control
    def start(self) -> None:
        if self._worker is not None:
            raise JobError("job already started")
        self._started_at = _now_iso()
        self._worker = threading.Thread(target=self._run,
                                        name=f"print-{self.job_id}", daemon=True)
        self._worker.start()

    def pause(self) -> None:
        if self.state is PrintJobState.RUNNING:
            self._unpaused.clear()
            logger.info("[%s] pause requested", self.job_id)

    def resume(self) -> None:
        self._unpaused.set()

    def stop(self) -> None:
        """Abort the job: preempts the in-flight move via the HAL (FR-JOB-03)."""
        logger.warning("[%s] stop requested", self.job_id)
        self._stop_requested = True
        self._unpaused.set()  # a paused job must observe the stop
        try:
            self.adapter.stop(StopMode.IMMEDIATE)
        except Exception:
            logger.exception("stop dispatch failed")

    def estop(self) -> None:
        logger.warning("[%s] ESTOP requested", self.job_id)
        self._estop_requested = True
        self._stop_requested = True
        self._unpaused.set()
        try:
            self.adapter.estop()
        except Exception:
            logger.exception("estop dispatch failed")

    def join(self, timeout: Optional[float] = None) -> bool:
        if self._worker is None:
            return True
        self._worker.join(timeout)
        return not self._worker.is_alive()

    # ---------------------------------------------------------------- worker
    def _run(self) -> None:
        self._set_state(PrintJobState.RUNNING)
        end_reason = "completed"
        try:
            total = len(self._actions)
            for index, action in enumerate(self._actions):
                if self._stop_requested:
                    end_reason = "estop" if self._estop_requested else "stopped"
                    self._set_state(PrintJobState.STOPPED)
                    return
                if not self._unpaused.is_set():
                    self._drain()  # finish in-flight moves before pausing
                    self._hold_paused()
                    if self._stop_requested:
                        end_reason = "estop" if self._estop_requested else "stopped"
                        self._set_state(PrintJobState.STOPPED)
                        return
                if action.line_no:
                    self.last_line = action.line_no
                self._emit(JobEvent(
                    kind="progress", job_id=self.job_id,
                    action_index=index, action_total=total,
                    line_no=action.line_no,
                    percent=100.0 * index / total if total else 100.0))
                self._execute(action)
            self._drain()
            if self._last_targets:
                self._verify_position(self._last_targets)
            self._set_state(PrintJobState.COMPLETED)
            self._emit(JobEvent(kind="progress", job_id=self.job_id,
                                action_index=len(self._actions),
                                action_total=len(self._actions),
                                line_no=self.last_line, percent=100.0))
        except Exception as exc:
            if self._stop_requested:
                # the in-flight move was cancelled by stop()/estop(): not an error
                end_reason = "estop" if self._estop_requested else "stopped"
                self._set_state(PrintJobState.STOPPED)
                return
            end_reason = "error"
            self.error_message = f"line {self.last_line}: {exc}"
            logger.error("[%s] job failed: %s", self.job_id, self.error_message)
            self._emit(JobEvent(kind="error", job_id=self.job_id,
                                message=self.error_message,
                                line_no=self.last_line))
            self._set_state(PrintJobState.ERROR)
        finally:
            self._finished_at = _now_iso()
            self._write_report(end_reason)

    def _hold_paused(self) -> None:
        self._set_state(PrintJobState.PAUSED)
        self._unpaused.wait()
        if not self._stop_requested:
            self._set_state(PrintJobState.RUNNING)

    # --------------------------------------------------------------- actions
    def _execute(self, action: Action) -> None:
        logger.debug("[%s] line %s: %s", self.job_id, action.line_no,
                     action.describe())
        if isinstance(action, MoveLinear):
            self._execute_move(action)
            return
        self._drain()  # non-move actions are barriers in the stream
        if isinstance(action, HomeAxes):
            self.adapter.home(self._home_order(action.axes))
            self._predicted = None  # homing moved the axes; re-read when needed
        elif isinstance(action, SelectTool):
            # v1: routing already resolved at interpret time; log-only until
            # the tool controller lands (PRD M5)
            logger.info("[%s] active tool -> T%d", self.job_id,
                        action.tool_index)
        elif isinstance(action, PauseProgram):
            self._emit(JobEvent(kind="warning", job_id=self.job_id,
                                line_no=action.line_no,
                                message=f"program pause ({action.reason}); "
                                        f"resume to continue"))
            self._unpaused.clear()
            self._hold_paused()
        elif isinstance(action, EndProgram):
            pass  # loop finishes naturally; kept for logging/reporting
        else:  # pragma: no cover - defensive
            raise JobError(f"unexecutable action {action!r}")

    def _home_order(self, axes) -> list[str]:
        """Tool Z axes first (SRS D11: they home NEGATIVE = physically UP,
        clearing the print plane before X/Y travel), then the others."""
        selected = list(axes) if axes is not None \
            else list(self.profile.axis_names)
        z_axes = {t.z_axis for t in self.profile.tools if t.z_axis}
        return ([a for a in selected if a in z_axes]
                + [a for a in selected if a not in z_axes])

    def _execute_move(self, move: MoveLinear) -> None:
        positions = self._current_positions()
        if move.relative:
            targets = {a: positions[a] + d for a, d in move.axes.items()}
        else:
            targets = dict(move.axes)
        if all(abs(targets[a] - positions.get(a, 0.0)) < _POSITION_EPSILON_MM
               for a in targets):
            return  # zero-length segment: nothing to do

        # a move touching a different axis set must not run concurrently with
        # queued predecessors (per-axis queues would not order them): drain
        axes_set = frozenset(targets)
        if self._inflight and axes_set != self._inflight_axes:
            self._drain()

        feed = self._clamped_feed(move)
        logger.debug("[%s] line %s: move %s @ %.1f mm/s", self.job_id,
                     move.line_no,
                     " ".join(f"{a}->{v:.3f}" for a, v in sorted(targets.items())),
                     feed)
        handle = self.adapter.move_async(targets, feed)
        self._inflight.append((handle, move.line_no, targets))
        self._inflight_axes = axes_set
        if self._predicted is None:
            self._predicted = dict(positions)
        self._predicted.update(targets)
        while len(self._inflight) >= self.stream_window:
            self._wait_oldest()

    def _current_positions(self) -> dict[str, float]:
        """Predicted end position of everything dispatched so far; falls back
        to a live read when nothing is known (start of job, after homing).
        The engine verifies every completed move to 0.5 mm, so predictions
        cannot drift silently."""
        if self._predicted is not None:
            return self._predicted
        return dict(self.adapter.get_status().positions)

    def _wait_oldest(self) -> None:
        handle, line_no, targets = self._inflight.popleft()
        newest = self.last_line
        if line_no:
            self.last_line = line_no  # error attribution for queued moves
        handle.wait()  # raises the move's MotionError on failure/cancel
        self.last_line = max(newest, line_no)  # success: restore progress
        self._last_targets = targets

    def _drain(self) -> None:
        while self._inflight:
            self._wait_oldest()
        self._inflight_axes = None

    def _verify_position(self, targets: dict[str, float]) -> None:
        """A completed move MUST be at its target (FR-JOB-04 hardening)."""
        after = self.adapter.get_status().positions
        mismatched = {a: after.get(a, float("nan")) for a in targets
                      if abs(after.get(a, float("nan")) - targets[a])
                      > self.POSITION_TOLERANCE_MM}
        if mismatched:
            detail = ", ".join(
                f"{a} commanded {targets[a]:.3f} but at {actual:.3f}"
                for a, actual in sorted(mismatched.items()))
            raise JobError(
                f"move reported complete but the machine is not at the "
                f"target ({detail}); the controller may have silently "
                f"refused the command")

    def _clamped_feed(self, move: MoveLinear) -> float:
        max_speeds = self.adapter.max_speeds()
        limit = min((max_speeds[a] for a in move.axes if a in max_speeds),
                    default=move.feed_mm_s)
        if move.feed_mm_s <= limit + 1e-9:
            return move.feed_mm_s
        if self.profile.policies.feed_over_max == POLICY_CLAMP:
            if not self._warned_clamp:
                self._emit(JobEvent(
                    kind="warning", job_id=self.job_id, line_no=move.line_no,
                    message=f"feed {move.feed_mm_s:.1f} mm/s exceeds axis max "
                            f"{limit:.1f} mm/s; clamping (further clamps not "
                            f"reported)"))
                self._warned_clamp = True
            return limit
        raise JobError(f"feed {move.feed_mm_s:.1f} mm/s exceeds axis max "
                       f"{limit:.1f} mm/s (policies.feed_over_max=error)")

    # ---------------------------------------------------------------- report
    def _write_report(self, end_reason: str) -> None:
        if self.report_path is None:
            return
        try:
            gcode_sha = hashlib.sha256(self.gcode_path.read_bytes()).hexdigest()
        except OSError:
            gcode_sha = None
        report = {
            "job_id": self.job_id,
            "gcode_path": str(self.gcode_path),
            "gcode_sha256": gcode_sha,
            "profile": self.profile.to_dict(),
            "mode": self.profile.mode_label,
            "started_at": self._started_at,
            "finished_at": self._finished_at,
            "end_reason": end_reason,
            "final_state": self.state.value,
            "last_line": self.last_line,
            "skipped_commands": dict(self.result.skipped),
            "warnings": [f"line {d.line_no}: {d.message}"
                         for d in self.result.warnings],
            "error": self.error_message,
        }
        try:
            self.report_path.parent.mkdir(parents=True, exist_ok=True)
            self.report_path.write_text(json.dumps(report, indent=2),
                                        encoding="utf-8")
            logger.info("[%s] report written: %s", self.job_id, self.report_path)
        except OSError:
            logger.exception("failed to write job report")


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


# ----------------------------------------------------------- worker sleep aid
def wait_for_state(runner: PrintJobRunner, state: PrintJobState,
                   timeout: float = 30.0, poll_s: float = 0.01) -> bool:
    """Test/CLI helper: block until the runner reaches ``state``."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if runner.state is state:
            return True
        time.sleep(poll_s)
    return runner.state is state

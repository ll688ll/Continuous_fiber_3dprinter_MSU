"""Duet execution strategies through the full PrintJobRunner (fake board)."""

from __future__ import annotations

import json

import pytest

from printer_msu.controllers.motion.duet.adapter import DuetMotionAdapter
from printer_msu.controllers.motion.duet.client import DuetClient
from printer_msu.core.gcode.interpreter import interpret
from printer_msu.core.gcode.parser import parse_text
from printer_msu.core.model.print_job import PrintJobState
from printer_msu.services.print_service import PrintJobRunner, wait_for_state

from duet_testlib import ScriptedDuetBoard
from printer_testlib import make_duet_profile

GCODE = "G28\nG1 X10 Y20 F600\nG1 X30 Y40 F600\n"


def _rig(gcode: str = GCODE, *, execution=None, board=None, profile=None,
         tmp_path=None):
    board = board or ScriptedDuetBoard()
    profile = profile or make_duet_profile()
    client = DuetClient("fake", opener=board)
    adapter = DuetMotionAdapter(client, profile.axes_on("duet"))
    adapter.POLL_S = 0.01
    adapter.IDLE_GRACE_S = 0.05
    adapter.connect()
    result = interpret(parse_text(gcode), profile)
    assert result.ok
    report_path = (tmp_path / "job.json") if tmp_path else None
    runner = PrintJobRunner(profile, adapter, result, "part.gcode",
                            job_id="job-test", report_path=report_path,
                            execution=execution)
    if runner._executor is not None:
        runner._executor.POLL_S = 0.01
        runner._executor.START_VERIFY_S = 2.0 \
            if hasattr(runner._executor, "START_VERIFY_S") else None
    return board, runner


def test_preload_is_the_default_executor():
    _, runner = _rig()
    from printer_msu.services.duet_execution import DuetFileExecutor

    assert isinstance(runner._executor, DuetFileExecutor)


def test_file_mode_uploads_starts_verifies_and_completes(tmp_path):
    board, runner = _rig(tmp_path=tmp_path)
    events = []
    runner.subscribe(events.append)
    runner.start()
    assert wait_for_state(runner, PrintJobState.COMPLETED, timeout=10.0)
    assert runner.join(5.0)

    # uploaded under the job id, M32'd, and the board really moved
    assert any(n.endswith("job-test.g") for n in board.files)
    assert any(c.startswith("M32") for c in board.command_log)
    assert board.axes["X"]["machinePosition"] == pytest.approx(60.0)  # 30+30
    assert board.axes["Y"]["machinePosition"] == pytest.approx(70.0)

    report = json.loads((tmp_path / "job.json").read_text())
    assert report["execution"] == "preload"
    assert report["board_duration_s"] is not None  # board time, not host time
    assert report["end_reason"] == "completed"

    percents = [e.percent for e in events if e.kind == "progress"]
    assert percents and percents[-1] == 100.0


def test_file_mode_refuses_ghost_job(tmp_path):
    """The 2026-08-25 bug: a swallowed M32 reply + the PREVIOUS job still
    running produced five minutes of fake measurements. Never again."""
    board = ScriptedDuetBoard()
    board.start_wrong_file = "0:/gcodes/old_job.g"
    _, runner = _rig(board=board, tmp_path=tmp_path)
    runner.start()
    assert wait_for_state(runner, PrintJobState.ERROR, timeout=10.0)
    assert "unknown job" in runner.error_message


def test_file_mode_graceful_stop_sends_m25_m0(tmp_path):
    board = ScriptedDuetBoard()
    board.job_reads_to_complete = 10_000  # a long job
    _, runner = _rig(board=board, tmp_path=tmp_path)
    runner.start()
    assert wait_for_state(runner, PrintJobState.RUNNING, timeout=5.0)
    import time

    time.sleep(0.1)  # let the job actually start
    runner.stop()
    assert wait_for_state(runner, PrintJobState.STOPPED, timeout=10.0)
    codes = [c.split()[0] for c in board.command_log]
    assert "M25" in codes and "M0" in codes
    assert "M112" not in codes  # graceful stop is not an e-stop


def test_file_mode_pause_resume(tmp_path):
    board = ScriptedDuetBoard()
    board.job_reads_to_complete = 50
    _, runner = _rig(board=board, tmp_path=tmp_path)
    runner.start()
    assert wait_for_state(runner, PrintJobState.RUNNING, timeout=5.0)
    runner.pause()
    assert wait_for_state(runner, PrintJobState.PAUSED, timeout=5.0)
    assert any(c.startswith("M25") for c in board.command_log)
    runner.resume()
    assert wait_for_state(runner, PrintJobState.COMPLETED, timeout=20.0)
    assert any(c.startswith("M24") for c in board.command_log)


def test_stream_mode_feeds_by_bytes_and_completes(tmp_path):
    board = ScriptedDuetBoard()
    board.drain_per_request = 120  # slow-ish drain: forces real flow control
    _, runner = _rig(board=board, execution="stream", tmp_path=tmp_path)
    runner.start()
    assert wait_for_state(runner, PrintJobState.COMPLETED, timeout=15.0)
    assert runner.join(5.0)
    assert board.overflow_dropped == 0  # byte-batching never overran the buffer
    assert board.axes["X"]["machinePosition"] == pytest.approx(60.0)
    report = json.loads((tmp_path / "job.json").read_text())
    assert report["execution"] == "stream"
    assert report["board_duration_s"] is None  # no board-side job object
    assert report["host_duration_s"] is not None


def test_stream_mode_stop_ends_stopped(tmp_path):
    board = ScriptedDuetBoard()
    board.drain_per_request = 4  # crawl so the job is still feeding
    long_gcode = "G28\n" + "".join(
        f"G1 X{10 + i} Y20 F600\n" for i in range(60))
    _, runner = _rig(long_gcode, board=board, execution="stream",
                     tmp_path=tmp_path)
    runner.start()
    assert wait_for_state(runner, PrintJobState.RUNNING, timeout=5.0)
    runner.stop()
    assert wait_for_state(runner, PrintJobState.STOPPED, timeout=10.0)


def test_end_position_mismatch_is_an_error(tmp_path):
    """A job that 'completes' with the machine off-target must fail loudly
    (the Duet equivalent of the FMC silent-no-motion defense)."""

    class LyingBoard(ScriptedDuetBoard):
        def _finish_job(self):
            super()._finish_job()
            self.axes["X"]["machinePosition"] = 3.0  # not where it should be

    _, runner = _rig(board=LyingBoard(), tmp_path=tmp_path)
    runner.start()
    assert wait_for_state(runner, PrintJobState.ERROR, timeout=10.0)
    assert "not at the final targets" in runner.error_message

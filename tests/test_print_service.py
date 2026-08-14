"""Preflight + streaming worker against the sim backend (FR-PRE, FR-JOB)."""

from __future__ import annotations

import json
import time

import pytest

pytest.importorskip("fmc_motion")

from printer_msu.core.gcode.interpreter import interpret
from printer_msu.core.gcode.parser import parse_text
from printer_msu.core.model.print_job import PrintJobState
from printer_msu.services.print_service import (
    PrintJobRunner,
    run_preflight,
    wait_for_state,
)

from printer_testlib import make_profile

SQUARE = """\
M104 S200
M109 S200
G21
G90
M82
G28 X Y
G92 E0
G1 F1500
G0 X10 Y10
G1 X30 Y10 E1.0
G1 X30 Y30 E2.0
G1 X10 Y30 E3.0
G1 X10 Y10 E4.0
M106 S255
G0 X5 Y5
M107
M84
M2
"""


def build(text, profile, adapter, tmp_path=None, **runner_kwargs):
    result = interpret(parse_text(text), profile)
    report_path = (tmp_path / "report.json") if tmp_path else None
    runner = PrintJobRunner(profile, adapter, result, "test.gcode",
                            report_path=report_path, **runner_kwargs)
    return result, runner


# ---------------------------------------------------------------- preflight
def test_preflight_passes_for_square(profile, adapter):
    result = interpret(parse_text(SQUARE), profile)
    report = run_preflight(profile, adapter, result)
    assert report.passed, [str(f) for f in report.findings]


def test_preflight_rejects_unhomed_without_g28(profile, adapter):
    result = interpret(parse_text("G1 X10 Y10 F600"), profile)
    report = run_preflight(profile, adapter, result)
    assert not report.passed
    assert any("not homed" in f.message for f in report.findings)
    # --home-first waives it
    report = run_preflight(profile, adapter, result, home_first=True)
    assert report.passed


def test_preflight_rejects_arcs(profile, adapter):
    result = interpret(parse_text("G28\nG2 X10 Y10 I5 J0 F600"), profile)
    report = run_preflight(profile, adapter, result)
    assert not report.passed


def test_preflight_feed_policy(profile, adapter):
    result = interpret(parse_text("G28\nG1 X10 F999999"), profile)
    report = run_preflight(profile, adapter, result)
    assert report.passed  # clamp policy: info only
    strict = make_profile(policies={"feed_over_max": "error"})
    report = run_preflight(strict, adapter, result)
    assert not report.passed


def test_preflight_estop_active(profile, adapter):
    adapter.estop()
    result = interpret(parse_text(SQUARE), profile)
    report = run_preflight(profile, adapter, result)
    assert not report.passed
    assert any("emergency" in f.message for f in report.findings)


def test_preflight_offline_mode(profile, adapter):
    result = interpret(parse_text(SQUARE), profile)
    report = run_preflight(profile, adapter, result, offline=True)
    assert report.passed  # file homes X/Y itself; no live checks needed


def test_preflight_envelope_unknown_limits_is_informational(profile, adapter):
    result = interpret(parse_text(SQUARE), profile)
    report = run_preflight(profile, adapter, result)
    assert report.passed
    assert any("no discovered soft limits" in f.message
               for f in report.findings if f.level == "info")


@pytest.fixture
def adapter_with_limits(profile):
    import copy

    import fmc_motion
    from printer_msu.controllers.motion.fmc_motion_adapter import FmcMotionAdapter

    from printer_testlib import FMC_SIM_CONFIG

    cfg = copy.deepcopy(FMC_SIM_CONFIG)
    for ax, hi in zip(cfg["axes"], (198.0, 148.0, 98.0)):
        ax["soft_limits"] = {"min": 2.0, "max": hi}
    adp = FmcMotionAdapter(fmc_motion.create_controller(cfg, mode="sim"),
                           axis_map=profile.motion.axis_map)
    adp.connect()
    yield adp
    adp.disconnect()


def test_preflight_envelope_flags_out_of_range_targets(profile,
                                                       adapter_with_limits):
    text = "G28\nG0 X500 Y10\nG1 X10 Y0.5 F600\nM2\n"
    result = interpret(parse_text(text), profile)
    report = run_preflight(profile, adapter_with_limits, result)
    assert not report.passed
    messages = [f.message for f in report.findings if f.level == "error"]
    assert any("axis 'X'" in m and "line 2" in m for m in messages)
    assert any("axis 'Y'" in m for m in messages)  # Y0.5 below soft min


def test_preflight_envelope_tracks_relative_moves(profile, adapter_with_limits):
    text = "G28\nG0 X190 Y10\nG91\nG1 X20 F600\nM2\n"  # 190+20 > 198
    result = interpret(parse_text(text), profile)
    report = run_preflight(profile, adapter_with_limits, result)
    assert not report.passed
    assert any("axis 'X'" in f.message and "line 4" in f.message
               for f in report.findings if f.level == "error")


def test_preflight_envelope_passes_in_range(profile, adapter_with_limits):
    text = "G28\nG0 X50 Y50\nG91\nG1 X5 Y5 F600\nM2\n"
    result = interpret(parse_text(text), profile)
    report = run_preflight(profile, adapter_with_limits, result)
    assert report.passed, [str(f) for f in report.findings]


# ------------------------------------------------------------------- runner
def test_square_prints_to_completion(profile, adapter, tmp_path):
    events = []
    result, runner = build(SQUARE, profile, adapter, tmp_path)
    runner.subscribe(events.append)
    runner.start()
    assert runner.join(timeout=30.0)
    assert runner.state is PrintJobState.COMPLETED

    positions = adapter.get_status().positions
    assert positions["X"] == pytest.approx(5.0, abs=0.05)
    assert positions["Y"] == pytest.approx(5.0, abs=0.05)

    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["final_state"] == "completed"
    assert report["end_reason"] == "completed"
    assert report["skipped_commands"]["M104"] == 1
    assert report["skipped_commands"]["E"] == 4
    assert report["last_line"] == 18

    states = [e.state for e in events if e.kind == "state"]
    assert states[0] is PrintJobState.RUNNING
    assert states[-1] is PrintJobState.COMPLETED
    assert any(e.kind == "progress" and e.percent == 100.0 for e in events)


def test_pause_and_resume(profile, adapter):
    text = "G28 X Y\nG1 X150 F1500\nG1 Y100\nG1 X10\nG1 Y10\nM2\n"
    result, runner = build(text, profile, adapter)
    runner.start()
    assert wait_for_state(runner, PrintJobState.RUNNING)
    runner.pause()
    assert wait_for_state(runner, PrintJobState.PAUSED, timeout=30.0)
    time.sleep(0.05)
    assert runner.state is PrintJobState.PAUSED
    runner.resume()
    assert runner.join(timeout=30.0)
    assert runner.state is PrintJobState.COMPLETED
    assert adapter.get_status().positions["X"] == pytest.approx(10.0, abs=0.05)


def test_m0_pauses_until_resume(profile, adapter):
    text = "G28 X Y\nG1 X50 F1500\nM0\nG1 X10\nM2\n"
    result, runner = build(text, profile, adapter)
    runner.start()
    assert wait_for_state(runner, PrintJobState.PAUSED, timeout=30.0)
    runner.resume()
    assert runner.join(timeout=30.0)
    assert runner.state is PrintJobState.COMPLETED


def test_stop_mid_print(profile, adapter):
    text = "G28 X Y\nG1 X190 Y140 F600\nG1 X10 Y10\nM2\n"
    result, runner = build(text, profile, adapter)
    runner.start()
    assert wait_for_state(runner, PrintJobState.RUNNING)
    time.sleep(0.1)  # let the long diagonal get going
    runner.stop()
    assert runner.join(timeout=30.0)
    assert runner.state is PrintJobState.STOPPED


def test_estop_mid_print(profile, adapter):
    text = "G28 X Y\nG1 X190 Y140 F600\nM2\n"
    result, runner = build(text, profile, adapter)
    runner.start()
    assert wait_for_state(runner, PrintJobState.RUNNING)
    time.sleep(0.1)
    runner.estop()
    assert runner.join(timeout=30.0)
    assert runner.state is PrintJobState.STOPPED
    assert adapter.get_status().emergency_active


def test_hardware_error_ends_in_error_state(profile, adapter, tmp_path):
    # no soft limits configured -> the move physically trips the limit switch
    text = "G28 X Y\nG1 X500 F1500\nM2\n"
    result, runner = build(text, profile, adapter, tmp_path)
    runner.start()
    assert runner.join(timeout=30.0)
    assert runner.state is PrintJobState.ERROR
    assert "line 2" in runner.error_message
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["end_reason"] == "error"
    assert report["error"] is not None


def test_home_all_homes_tool_z_first(profile, adapter):
    """SRS D11: Z retracts up (negative-limit home) before X/Y move."""
    result, runner = build("G28\nM2\n", profile, adapter)
    runner.start()
    assert runner.join(timeout=30.0)
    assert runner.state is PrintJobState.COMPLETED
    home_axes = [h["axis"] for h in adapter._ctrl.history if h["op"] == "home"]
    assert home_axes == [2, 0, 1]  # Z_T0 first, then X, Y


def test_home_first_prepends_homing(profile, adapter):
    text = "G1 X10 Y10 F600\nM2\n"
    result, runner = build(text, profile, adapter, home_first=True)
    runner.start()
    assert runner.join(timeout=30.0)
    assert runner.state is PrintJobState.COMPLETED
    assert adapter.get_status().positions["X"] == pytest.approx(10.0, abs=0.05)


def test_relative_moves_resolve_against_live_position(profile, adapter):
    text = "G28 X Y\nG0 X20 Y20\nG91\nG1 X5 Y5 F600\nG1 X5 F600\nM2\n"
    result, runner = build(text, profile, adapter)
    runner.start()
    assert runner.join(timeout=30.0)
    assert runner.state is PrintJobState.COMPLETED
    positions = adapter.get_status().positions
    assert positions["X"] == pytest.approx(30.0, abs=0.05)
    assert positions["Y"] == pytest.approx(25.0, abs=0.05)


class _FakeDoneHandle:
    def wait(self, timeout=None):
        return True  # "completed" without motion


class _SilentlyIgnoringAdapter:
    """Reproduces the 2026-08-12 lab failure: the controller accepts move
    commands (rc=0) but never moves; everything else is passed through."""

    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        return getattr(self._real, name)

    def move_absolute(self, axes_positions, feed, accel=None, decel=None):
        pass  # accepted, "completed", no motion

    def move_async(self, axes_positions, feed, accel=None, decel=None):
        return _FakeDoneHandle()  # accepted, "completed", no motion


def test_silent_no_motion_is_detected_as_error(profile, adapter, tmp_path):
    silent = _SilentlyIgnoringAdapter(adapter)
    text = "G28 X Y\nG0 X10 Y10\nM2\n"
    result = interpret(parse_text(text), profile)
    runner = PrintJobRunner(profile, silent, result, "test.gcode",
                            report_path=tmp_path / "report.json")
    runner.start()
    assert runner.join(timeout=30.0)
    assert runner.state is PrintJobState.ERROR
    assert "not at the target" in runner.error_message


def test_xy_moves_use_line_interpolation(profile, adapter):
    result, runner = build("G28 X Y\nG0 X40 Y40\nM2\n", profile, adapter)
    runner.start()
    assert runner.join(timeout=30.0)
    ctrl = adapter._ctrl
    assert any(h["op"] == "line" for h in ctrl.history)


# ---------------------------------------------------------------- streaming
def test_streamed_xy_moves_complete_in_order(profile, adapter):
    """Consecutive XY segments are dispatched ahead (stream_window) and the
    per-axis queues execute them in order to the correct final position."""
    text = ("G28\nG0 X50 Y50\n"
            + "".join(f"G1 X{50 + i} Y{50 + i} F1500\n" for i in range(1, 9))
            + "M2\n")
    result, runner = build(text, profile, adapter)
    runner.start()
    assert runner.join(timeout=30.0)
    assert runner.state is PrintJobState.COMPLETED
    positions = adapter.get_status().positions
    assert positions["X"] == pytest.approx(58.0, abs=0.05)
    assert positions["Y"] == pytest.approx(58.0, abs=0.05)
    line_ops = [h for h in adapter._ctrl.history if h["op"] == "line"]
    assert len(line_ops) == 9  # travel + 8 segments, none skipped


def test_streaming_barrier_on_axis_set_change(profile, adapter):
    """A Z move must not run concurrently with queued XY lines: the runner
    drains before switching axis sets (per-axis queues alone cannot order
    them). The Z dispatch must happen only after the XY line finished."""
    text = "G28\nG0 X50 Y50\nG1 X150 Y140 F600\nG1 Z90 F600\nM2\n"
    result, runner = build(text, profile, adapter)
    runner.start()
    assert runner.join(timeout=60.0)
    assert runner.state is PrintJobState.COMPLETED, runner.error_message
    history = adapter._ctrl.history
    xy_line = [h for h in history if h["op"] == "line"
               and h["targets"] == {0: 150.0, 1: 140.0}][0]
    z_move = [h for h in history if h["op"] == "move" and h["axis"] == 2][-1]
    # XY line: ~134 mm path at 10 mm/s composite ≈ 13 sim-s; the Z dispatch
    # time must lie beyond the line's completion, not overlap its start
    assert z_move["t"] >= xy_line["t"] + 10.0
    assert adapter.get_status().positions["Z_T0"] == pytest.approx(90.0,
                                                                   abs=0.05)


def test_streamed_error_attributes_failing_line(profile, adapter, tmp_path):
    # the SECOND queued move physically trips the limit (travel is 200 mm)
    text = "G28\nG0 X50 Y50\nG1 X190 Y50 F1500\nG1 X500 Y50 F1500\nM2\n"
    result, runner = build(text, profile, adapter, tmp_path)
    runner.start()
    assert runner.join(timeout=30.0)
    assert runner.state is PrintJobState.ERROR
    assert "line 4" in runner.error_message


def test_stream_window_one_behaves_like_blocking(adapter):
    profile = make_profile(policies={"stream_window": 1})
    text = "G28\nG0 X40 Y40\nG1 X60 Y60 F1500\nM2\n"
    result, runner = build(text, profile, adapter)
    runner.start()
    assert runner.join(timeout=30.0)
    assert runner.state is PrintJobState.COMPLETED
    assert adapter.get_status().positions["X"] == pytest.approx(60.0, abs=0.05)


def test_pause_drains_streamed_moves(profile, adapter):
    text = ("G28\nG0 X10 Y10\n"
            + "".join(f"G1 X{10 + 20 * i} Y10 F1500\n" for i in range(1, 8))
            + "M2\n")
    result, runner = build(text, profile, adapter)
    runner.start()
    assert wait_for_state(runner, PrintJobState.RUNNING)
    runner.pause()
    assert wait_for_state(runner, PrintJobState.PAUSED, timeout=30.0)
    # paused between actions with nothing in flight: axes are idle
    status = adapter.get_status()
    assert status.run_state == "idle"
    runner.resume()
    assert runner.join(timeout=30.0)
    assert runner.state is PrintJobState.COMPLETED

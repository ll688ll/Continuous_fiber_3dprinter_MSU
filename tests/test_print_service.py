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


class _SilentlyIgnoringAdapter:
    """Reproduces the 2026-08-12 lab failure: the controller accepts move
    commands (rc=0) but never moves; everything else is passed through."""

    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        return getattr(self._real, name)

    def move_absolute(self, axes_positions, feed, accel=None, decel=None):
        pass  # accepted, "completed", no motion


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

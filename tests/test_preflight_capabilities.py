"""V2-M5: capability-gap preflight + capability-gated arcs (D5)."""

from __future__ import annotations

import pytest

from printer_msu.core.gcode.analysis import analyze
from printer_msu.core.gcode.emitter import emit_machine_gcode
from printer_msu.core.gcode.interpreter import interpret
from printer_msu.core.gcode.parser import parse_text
from printer_msu.core.model.actions import MoveArc
from printer_msu.services.print_service import run_preflight

from printer_testlib import make_duet_profile, make_profile

HEATED_FILE = ("T0\nG28\nM104 S200\nM109 S200\nM106 S255\n"
               "G1 X10 Y10 E1.5 F600\nM107\nM105\n")


def _preflight_offline(gcode: str, profile):
    result = interpret(parse_text(gcode), profile)
    return run_preflight(profile, adapter=None, result=result, offline=True)


# ------------------------------------------------------------ gap reporting
def test_gap_report_names_missing_heater_extruder_fan():
    report = _preflight_offline(HEATED_FILE, make_duet_profile())
    text = " | ".join(str(f) for f in report.findings)
    assert "no heater is configured" in text
    assert "WITHOUT extrusion" in text
    assert "no fan is configured" in text
    assert report.passed  # gaps are warnings under the default skip policy


def test_no_gaps_when_hardware_is_configured():
    from test_tool_functions import _tooled_profile

    report = _preflight_offline(HEATED_FILE, _tooled_profile())
    text = " | ".join(str(f) for f in report.findings)
    assert "no heater" not in text and "WITHOUT extrusion" not in text


def test_gap_report_is_offline():
    """Requirement 5: the review happens right after loading the file — no
    controller connection needed."""
    report = _preflight_offline("M104 S200\nG1 X5 F600\n", make_duet_profile())
    assert any("no heater" in str(f) for f in report.findings)


def test_motion_only_file_reports_no_gaps():
    report = _preflight_offline("G28\nG1 X10 Y10 F600\n", make_duet_profile())
    assert not any("configured" in str(f) for f in report.findings)


# -------------------------------------------------------------- arcs (D5)
def test_arcs_accepted_on_duet():
    result = interpret(parse_text("G28\nG1 X10 Y10 F600\n"
                                  "G2 X20 Y10 I5 J0 F600\n"),
                       make_duet_profile())
    assert result.ok
    arcs = [a for a in result.actions if isinstance(a, MoveArc)]
    assert len(arcs) == 1
    # endpoint workspace-shifted (+30), I/J offsets untouched
    assert arcs[0].axes == {"X": pytest.approx(50.0), "Y": pytest.approx(40.0)}
    assert arcs[0].i_offset == 5.0 and arcs[0].j_offset == 0.0
    assert arcs[0].clockwise


def test_arcs_still_fatal_on_fmc():
    result = interpret(parse_text("G2 X20 Y10 I5 J0 F600\n"), make_profile())
    assert not result.ok
    assert "arcs are not supported" in result.errors[0].message


def test_arc_policy_error_overrides_capability():
    profile = make_duet_profile(policies={"arcs": "error"})
    result = interpret(parse_text("G2 X20 Y10 I5 J0 F600\n"), profile)
    assert not result.ok


def test_helical_and_relative_and_full_circle_arcs_rejected():
    profile = make_duet_profile()
    assert not interpret(parse_text("G2 X10 Y10 Z5 I5 J0 F600\n"), profile).ok
    assert not interpret(parse_text("G91\nG2 X10 Y10 I5 J0 F600\n"), profile).ok
    assert not interpret(parse_text("G2 I5 J0 F600\n"), profile).ok
    assert not interpret(parse_text("G2 X10 Y10 F600\n"), profile).ok  # no I/J/R


def test_arc_emitted_as_g2_with_offsets():
    profile = make_duet_profile()
    result = interpret(parse_text("G28\nG3 X10 Y20 I0 J5 F600\n"), profile)
    assert result.ok
    program = emit_machine_gcode(list(result.actions), profile)
    arc_line = [l for l in program.lines if l.startswith("G3")][0]
    assert arc_line == "G3 X40 Y50 I0 J5 F600"
    assert program.final_targets["X"] == pytest.approx(40.0)


def test_arc_envelope_uses_radius_band():
    """An arc whose endpoint is legal but whose sweep leaves the envelope
    must still be flagged (conservative radius band)."""
    profile = make_duet_profile()
    result = interpret(
        parse_text("G28\nG1 X5 Y5 F600\nG2 X6 Y5 I500 J0 F600\n"), profile)
    assert result.ok

    class _Limits:
        def get_status(self):
            class S:  # minimal duck-typed status
                emergency_active = False
                positions = {}
            return S()

        def homed_axes(self):
            return {"X", "Y", "Z_T0"}

        def max_speeds(self):
            return {"X": 200.0, "Y": 200.0, "Z_T0": 100.0}

        def soft_limits(self):
            return {"X": (0.0, 987.448), "Y": (0.0, 290.269),
                    "Z_T0": (0.0, 282.306)}

    report = run_preflight(profile, _Limits(), result)
    assert not report.passed
    assert any("outside axis" in str(f) for f in report.findings)


# ----------------------------------------------------------- usage analysis
def test_usage_report_needs_flags():
    usage = analyze(parse_text(HEATED_FILE))
    assert usage.needs_heater and usage.needs_fan and usage.extrudes
    assert usage.reports_temp
    assert "heater" in usage.summary()
    quiet = analyze(parse_text("G28\nG1 X5 F600\n"))
    assert not (quiet.needs_heater or quiet.needs_fan or quiet.extrudes)

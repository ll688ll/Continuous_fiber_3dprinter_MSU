"""Interpreter semantics (printer PRD FR-INT-01..07)."""

from __future__ import annotations

import pytest

from printer_msu.core.gcode.interpreter import interpret
from printer_msu.core.gcode.parser import parse_text
from printer_msu.core.model.actions import (
    EndProgram,
    HomeAxes,
    MoveLinear,
    PauseProgram,
    SelectTool,
    SkippedCommand,
)

from printer_testlib import make_profile


def run(text: str, profile=None):
    return interpret(parse_text(text), profile or make_profile())


def moves(result):
    return [a for a in result.actions if isinstance(a, MoveLinear)]


# ------------------------------------------------------------------- motion
def test_absolute_xy_move_with_feed_conversion():
    result = run("G90\nG1 X10 Y20 F1500")
    (move,) = moves(result)
    assert move.axes == {"X": 10.0, "Y": 20.0}
    assert move.feed_mm_s == pytest.approx(25.0)  # 1500 mm/min
    assert not move.relative and not move.rapid
    assert result.ok


def test_g0_uses_travel_feed_before_any_f():
    result = run("G0 X5")
    (move,) = moves(result)
    assert move.rapid
    assert move.feed_mm_s == pytest.approx(3000.0 / 60.0)


def test_g0_prefers_modal_feed():
    # slicers put the travel speed on the G0 line itself (RepRap dialect)
    result = run("G0 F7500 X5\nG0 X10")
    first, second = moves(result)
    assert first.feed_mm_s == pytest.approx(125.0)
    assert second.feed_mm_s == pytest.approx(125.0)  # modal, wins over profile


def test_default_feed_when_no_f_seen():
    result = run("G1 X5")
    (move,) = moves(result)
    assert move.feed_mm_s == pytest.approx(1200.0 / 60.0)


def test_feed_is_modal():
    result = run("G1 X5 F600\nG1 X10")
    assert [m.feed_mm_s for m in moves(result)] == [pytest.approx(10.0)] * 2


def test_relative_mode():
    result = run("G91\nG1 X5 Y-2 F600")
    (move,) = moves(result)
    assert move.relative
    assert move.axes == {"X": 5.0, "Y": -2.0}


def test_sticky_motion_mode():
    result = run("G1 X5 F600\nX10 Y3")
    assert len(moves(result)) == 2
    assert moves(result)[1].axes == {"X": 10.0, "Y": 3.0}


def test_bare_axis_words_without_prior_motion_is_error():
    result = run("X10")
    assert not result.ok
    assert "no active motion command" in result.errors[0].message


def test_zero_feed_is_error():
    result = run("G1 X5 F0")
    assert not result.ok


# ---------------------------------------------------------------- Z routing
def test_z_routes_to_active_tool_axis():
    result = run("T0\nG1 Z5 F600")
    select, move = result.actions
    assert isinstance(select, SelectTool)
    assert move.axes == {"Z_T0": 5.0}


def test_z_before_tool_uses_default_tool_with_warning():
    result = run("G1 Z5 F600")
    (move,) = moves(result)
    assert move.axes == {"Z_T0": 5.0}
    assert any("assuming" in d.message for d in result.warnings)
    assert result.ok


def test_xyz_move_split_z_first_with_warning():
    result = run("T0\nG1 X10 Y10 Z2 F600")
    ms = moves(result)
    assert len(ms) == 2
    assert ms[0].axes == {"Z_T0": 2.0}
    assert ms[1].axes == {"X": 10.0, "Y": 10.0}
    assert any("split Z-first" in d.message for d in result.warnings)


def test_unconfigured_tool_is_fatal():
    result = run("T5")
    assert not result.ok
    assert "not configured" in result.errors[0].message


def test_t_on_gm_lines_is_a_parameter_not_a_tool_change():
    # Cura: "M204 P500 R1000 T500" (travel accel) must NOT select T500
    result = run("M204 P500.00 R1000.00 T500.00")
    assert result.ok
    assert not any(isinstance(a, SelectTool) for a in result.actions)
    result = run("G1 X5 T3 F600")  # pathological but must not tool-change
    assert not any(isinstance(a, SelectTool) for a in result.actions)


# ---------------------------------------------------------------- workspace
def test_xy_offset_applied_to_absolute_moves_only():
    profile = make_profile(workspace={"xy_offset": {"X": 30.0, "Y": 20.0}})
    result = run("G1 X10 Y10 F600\nG91\nG1 X5 Y5", profile)
    absolute, rel = moves(result)
    assert absolute.axes == {"X": 40.0, "Y": 30.0}
    assert rel.axes == {"X": 5.0, "Y": 5.0}  # deltas unshifted


def test_z_bed_mode_inverts_and_offsets():
    profile = make_profile(workspace={"z_mode": "bed", "z_bed_offset_mm": 250.0})
    result = run("T0\nG1 Z0.2 F600\nG91\nG1 Z0.4\nG1 Z-0.1", profile)
    z_moves = moves(result)
    assert z_moves[0].axes == {"Z_T0": 249.8}   # 250 - 0.2
    assert z_moves[1].axes == {"Z_T0": -0.4}    # raise 0.4 above bed = machine down-count
    assert z_moves[2].axes == {"Z_T0": 0.1}
    assert result.ok


def test_z_machine_mode_passes_through():
    result = run("T0\nG1 Z10 F600")
    assert moves(result)[0].axes == {"Z_T0": 10.0}


def test_tool_without_z_axis_error_policy():
    profile = make_profile(tools=[{"tool_index": 0, "z_axis": None}])
    result = run("T0\nG1 Z5 F600", profile)
    assert not result.ok


def test_tool_without_z_axis_skip_policy_drops_z():
    profile = make_profile(tools=[{"tool_index": 0, "z_axis": None}],
                           policies={"missing_axis": "skip"})
    result = run("T0\nG1 X10 Z5 F600", profile)
    (move,) = moves(result)
    assert move.axes == {"X": 10.0}
    assert result.ok


# ------------------------------------------------------- motion-only (D2)
def test_tool_commands_skipped_with_tally():
    result = run("M104 S200\nM109 S200\nM106 S255\nM107\nM105")
    assert result.ok
    assert all(isinstance(a, SkippedCommand) for a in result.actions)
    assert result.skipped["M104"] == 1
    assert result.skipped["M109"] == 1


def test_tool_commands_error_policy():
    profile = make_profile(policies={"tool_commands": "error"})
    result = run("M104 S200", profile)
    assert not result.ok


def test_e_words_skipped_but_motion_executes():
    result = run("G1 X10 Y10 E2.5 F600")
    (move,) = moves(result)
    assert move.axes == {"X": 10.0, "Y": 10.0}
    assert result.skipped["E"] == 1
    assert result.ok


def test_e_only_move_produces_no_motion():
    result = run("G92 E0\nG1 E5 F600")
    assert moves(result) == []
    assert result.ok


def test_g92_e_silent_but_g92_xyz_warns():
    result = run("G92 E0")
    assert result.skipped == {}
    result = run("G92 X0 Y0")
    assert result.skipped["G92 XY"] == 1


# ------------------------------------------------------------------ dialect
def test_g28_all_and_named():
    result = run("G28")
    assert result.actions == [HomeAxes(line_no=1, axes=None)]
    result = run("G28 X Y")  # slicer-style bare axis flags
    (home,) = [a for a in result.actions if isinstance(a, HomeAxes)]
    assert home.axes == ("X", "Y")
    assert result.ok


def test_g28_z_routes_through_tool():
    result = run("T0\nG28 Z")
    (home,) = [a for a in result.actions if isinstance(a, HomeAxes)]
    assert home.axes == ("Z_T0",)


def test_bare_axis_flag_in_motion_is_error():
    result = run("G1 X F600")
    assert not result.ok
    assert "requires a value" in result.errors[0].message


def test_arcs_are_fatal():
    result = run("G2 X10 Y10 I5 J0 F600")
    assert not result.ok
    assert "arcs" in result.errors[0].message


def test_inches_are_fatal():
    result = run("G20\nG1 X1 F600")
    assert not result.ok


def test_pause_and_end():
    result = run("G1 X5 F600\nM0\nG1 X10\nM2\nG1 X99")
    kinds = [type(a).__name__ for a in result.actions]
    assert kinds == ["MoveLinear", "PauseProgram", "MoveLinear", "EndProgram"]
    # nothing after M2 is interpreted
    assert all(a.line_no <= 4 for a in result.actions)


def test_unknown_command_warn_and_error_policies():
    result = run("M999")
    assert result.ok and result.skipped["M999"] == 1
    strict = make_profile(policies={"unknown_command": "error"})
    result = run("M999", strict)
    assert not result.ok


def test_parse_errors_become_fatal_diagnostics():
    result = run("G1 X10 garbage")
    assert not result.ok

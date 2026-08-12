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


def test_g0_uses_travel_feed():
    result = run("G0 X5")
    (move,) = moves(result)
    assert move.rapid
    assert move.feed_mm_s == pytest.approx(3000.0 / 60.0)


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

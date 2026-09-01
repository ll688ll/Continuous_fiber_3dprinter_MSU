"""emit_machine_gcode: actions -> board-ready program (V2-M3)."""

from __future__ import annotations

import pytest

from printer_msu.core.gcode.emitter import EmitError, emit_machine_gcode
from printer_msu.core.gcode.interpreter import interpret
from printer_msu.core.gcode.parser import parse_text

from printer_testlib import make_duet_profile, make_profile


def _program(text: str, profile=None):
    profile = profile or make_duet_profile()
    result = interpret(parse_text(text), profile)
    assert result.ok, [d.message for d in result.errors]
    return emit_machine_gcode(list(result.actions), profile), result


def test_moves_emit_letters_and_mm_min_feed():
    program, _ = _program("G28\nG1 X10 Y20 F600\n")
    body = [l for l in program.lines if not l.startswith(";")]
    assert body[0] == "G21" and body[1] == "G90"
    assert "G28" in body
    move = [l for l in body if l.startswith("G1")][0]
    # workspace xy_offset +30 applied by the interpreter; F600 mm/min = 10 mm/s
    assert move == "G1 X40 Y50 F600"


def test_z_routes_to_tool_letter_and_plane_transform():
    program, _ = _program("T0\nG1 Z10 F300\n")
    move = [l for l in program.lines if l.startswith("G1")][0]
    # bed mode: machine_z = plane_offset(100) - 10 = 90; letter Z
    assert move == "G1 Z90 F300"


def test_relative_blocks_wrapped_and_restored():
    program, _ = _program("G28\nG91\nG1 X5 F600\nG90\nG1 X10 F600\n")
    body = [l for l in program.lines if not l.startswith(";")]
    i_rel = body.index("G91")
    assert body[i_rel + 1] == "G1 X5 F600"
    assert "G90" in body[i_rel + 2:]


def test_final_targets_track_absolute_axes_only():
    program, _ = _program("G28\nG1 X10 Y20 F600\nG91\nG1 X5 F600\nG90\n")
    # X: 40 absolute then +5 relative on a known base -> 45; Y stays 50
    assert program.final_targets["X"] == pytest.approx(45.0)
    assert program.final_targets["Y"] == pytest.approx(50.0)


def test_homing_clears_final_targets_and_pause_maps_to_m226():
    program, _ = _program("G1 X10 F600\nG28 X\nM0\n")
    assert "X" not in program.final_targets
    assert any(l.startswith("M226") for l in program.lines)


def test_partial_home_emits_tool_z_first():
    program, _ = _program("T0\nG28 X Z\n")
    g28s = [l for l in program.lines if l.startswith("G28")]
    assert g28s == ["G28 Z", "G28 X"]  # SRS D11: tool Z clears the plane first


def test_source_line_mapping_by_byte_offset():
    program, _ = _program("G28\nG1 X10 F600\nG1 X20 F600\n")
    # find the byte offset of the second move; the map must attribute it
    idx = [i for i, l in enumerate(program.lines) if l.startswith("G1")][1]
    offset = program.byte_offsets[idx]
    assert program.source_line_at_offset(offset) == program.source_lines[idx]
    assert program.source_line_at_offset(0) == 0  # header


def test_fmc_profile_without_letters_is_rejected():
    profile = make_profile()  # fmc axes carry no letters
    result = interpret(parse_text("G1 X10 F600\n"), profile)
    with pytest.raises(EmitError, match="no controller letter"):
        emit_machine_gcode(list(result.actions), profile)

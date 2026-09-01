"""V2-M4: capability-driven tool dispatch (interpreter/emitter) + Duet tools."""

from __future__ import annotations

import copy

import pytest

from printer_msu.config.profile import profile_from_dict
from printer_msu.controllers.motion.duet.client import DuetClient, DuetError
from printer_msu.controllers.tool.duet_tool_controller import DuetToolController
from printer_msu.core.gcode.emitter import emit_machine_gcode
from printer_msu.core.gcode.interpreter import interpret
from printer_msu.core.gcode.parser import parse_text
from printer_msu.core.model.actions import MoveLinear, SetFanSpeed, SetToolTemp

from duet_testlib import ScriptedDuetBoard
from printer_testlib import DUET_PROFILE_DATA, make_duet_profile, make_profile


def _tooled_profile(**tool_extra):
    data = copy.deepcopy(DUET_PROFILE_DATA)
    data["tools"][0].update({
        "extruder": {"controller": "duet", "channel": 3,
                     "steps_per_mm": 400.0, "max_speed_mm_s": 60.0,
                     "max_accel_mm_s2": 500.0},
        "heater": {"controller": "duet", "heater_index": 0,
                   "output_pin": "out0",
                   "sensor": {"sensor_index": 0,
                              "type": "thermocouple-max31856",
                              "port": "spi.cs0"},
                   "max_temp_c": 300.0},
        "fan": {"controller": "duet", "fan_index": 0, "output_pin": "out7"},
    })
    data["tools"][0].update(tool_extra)
    return profile_from_dict(data)


# ------------------------------------------------------------- interpreter
def test_heater_configured_m104_m109_become_actions():
    result = interpret(parse_text("M104 S200\nM109 S210\n"), _tooled_profile())
    assert result.ok
    temps = [a for a in result.actions if isinstance(a, SetToolTemp)]
    assert [(t.temp_c, t.wait) for t in temps] == [(200.0, False),
                                                   (210.0, True)]


def test_no_heater_keeps_v1_skip_policy():
    profile = make_duet_profile()  # no tool hardware
    result = interpret(parse_text("M104 S200\n"), profile)
    assert result.ok
    assert not any(isinstance(a, SetToolTemp) for a in result.actions)
    assert result.skipped["M104"] == 1


def test_overtemp_is_fatal():
    result = interpret(parse_text("M104 S400\n"), _tooled_profile())
    assert not result.ok
    assert "max_temp_c" in result.errors[0].message


def test_extrusion_rides_the_xy_component():
    gcode = "T0\nG28\nM83\nG1 X10 Y10 Z5 E2.5 F600\n"
    result = interpret(parse_text(gcode), _tooled_profile())
    assert result.ok
    moves = [a for a in result.actions if isinstance(a, MoveLinear)]
    z_move = [m for m in moves if "Z_T0" in m.axes][0]
    xy_move = [m for m in moves if "X" in m.axes][0]
    assert z_move.e_delta is None          # never on the Z-only component
    assert xy_move.e_delta == pytest.approx(2.5)


def test_absolute_e_with_g92_normalizes_to_deltas():
    gcode = ("T0\nG28\nM82\nG92 E0\nG1 X10 E5 F600\n"
             "G1 X20 E12 F600\nG92 E0\nG1 X30 E3 F600\n")
    result = interpret(parse_text(gcode), _tooled_profile())
    assert result.ok
    deltas = [a.e_delta for a in result.actions
              if isinstance(a, MoveLinear) and a.e_delta is not None]
    assert deltas == pytest.approx([5.0, 7.0, 3.0])


def test_extrusion_only_move_is_emitted():
    result = interpret(parse_text("T0\nG1 E-2 F1800\n"), _tooled_profile())
    assert result.ok
    retract = [a for a in result.actions if isinstance(a, MoveLinear)][0]
    assert retract.axes == {} and retract.e_delta == pytest.approx(-2.0)


def test_fan_normalized_0_255_and_off():
    result = interpret(parse_text("M106 S127.5\nM107\n"), _tooled_profile())
    fans = [a for a in result.actions if isinstance(a, SetFanSpeed)]
    assert fans[0].value == pytest.approx(0.5)
    assert fans[1].value == 0.0


def test_fmc_profile_behavior_unchanged():
    """The whole point of capability dispatch: FMC files still skip."""
    profile = make_profile()
    result = interpret(parse_text("M104 S200\nM106 S255\nG1 X5 E1 F600\n"),
                       profile)
    assert result.ok
    assert not any(isinstance(a, (SetToolTemp, SetFanSpeed))
                   for a in result.actions)
    assert result.skipped["M104"] == 1 and result.skipped["M106"] == 1
    assert all(m.e_delta is None for m in result.actions
               if isinstance(m, MoveLinear))


# ----------------------------------------------------------------- emitter
def test_emitter_renders_tool_actions():
    profile = _tooled_profile()
    gcode = "T0\nG28\nM104 S200\nM109 S210\nM106 S255\nG1 X10 E1.5 F600\nM107\n"
    result = interpret(parse_text(gcode), profile)
    assert result.ok
    program = emit_machine_gcode(list(result.actions), profile)
    text = program.text()
    assert "M83" in text                      # deltas need relative extrusion
    assert "T0" in program.lines              # real tool select now
    assert "M104 T0 S200" in text and "M109 T0 S210" in text
    assert "M106 P0 S1" in text and "M107 P0" in text
    move = [l for l in program.lines if l.startswith("G1") and "E" in l][0]
    assert "E1.5" in move and "X40" in move   # xy_offset +30 still applies


# ------------------------------------------------------- DuetToolController
def _tool_rig():
    board = ScriptedDuetBoard()
    profile = _tooled_profile()
    client = DuetClient("fake", opener=board)
    client.connect()
    tool = DuetToolController(client, profile, "duet")
    tool.connect()
    return board, tool


def test_set_temperature_and_ceiling():
    board, tool = _tool_rig()
    tool.set_target_temperature(0, 200.0)
    assert "M104 T0 S200" in board.command_log
    with pytest.raises(DuetError, match="max_temp_c"):
        tool.set_target_temperature(0, 400.0)


def test_fan_and_heater_off():
    board, tool = _tool_rig()
    tool.set_fan_speed(0, 0.5)
    tool.set_fan_speed(0, 0.0)
    tool.heater_off(0)
    assert "M106 P0 S0.5" in board.command_log
    assert "M107 P0" in board.command_log
    assert "M104 T0 S0" in board.command_log


def test_emergency_shutdown_kills_heater_and_fan():
    board, tool = _tool_rig()
    tool.emergency_shutdown()
    assert "M104 T0 S0" in board.command_log
    assert "M107 P0" in board.command_log


def test_unknown_tool_channel_rejected():
    _, tool = _tool_rig()
    with pytest.raises(DuetError, match="tool 5"):
        tool.set_target_temperature(5, 100.0)


def test_build_machine_registers_tool_controller():
    from printer_msu.machine import build_machine

    machine = build_machine(_tooled_profile())
    assert "duet" in machine.tool_controllers
    assert machine.motion.tool_controller is machine.tool_controllers["duet"]

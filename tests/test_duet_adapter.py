"""DuetMotionAdapter behavior against the fake board."""

from __future__ import annotations

import pytest

from printer_msu.controllers.motion.base_motion_controller import StopMode
from printer_msu.controllers.motion.duet.adapter import DuetMotionAdapter
from printer_msu.controllers.motion.duet.client import DuetClient, DuetError
from printer_msu.machine import build_machine

from duet_testlib import ScriptedDuetBoard
from printer_testlib import make_duet_profile


@pytest.fixture()
def rig():
    board = ScriptedDuetBoard()
    profile = make_duet_profile()
    client = DuetClient("fake", opener=board)
    duet_axes = profile.axes_on("duet")
    adapter = DuetMotionAdapter(client, duet_axes)
    adapter.POLL_S = 0.01
    adapter.IDLE_GRACE_S = 0.05
    adapter.connect()
    return board, adapter


def test_connect_clean_board_has_no_drift(rig):
    board, adapter = rig
    assert adapter.drift_findings == []


def test_drift_detected_on_scale_and_limit_mismatch():
    board = ScriptedDuetBoard()
    board.axes["X"]["stepsPerMm"] = 80.0        # wrong scale
    board.axes["Y"]["max"] = 200.0              # wrong soft limit
    profile = make_duet_profile()
    adapter = DuetMotionAdapter(DuetClient("fake", opener=board),
                                profile.axes_on("duet"))
    adapter.connect()
    joined = " | ".join(adapter.drift_findings)
    assert "stepsPerMm" in joined and "scale mismatch" in joined
    assert "max=200.0" in joined


def test_capabilities_route_prints_to_file_execution(rig):
    _, adapter = rig
    caps = adapter.capabilities()
    assert caps["execution"] == "gcode_file"
    assert caps["arcs"] is True
    with pytest.raises(NotImplementedError):
        adapter.move_async({"X": 10.0}, 20.0)


def test_home_all_uses_bare_g28(rig):
    board, adapter = rig
    adapter.home()
    assert "G28" in board.command_log
    assert adapter.homed_axes() == {"X", "Y", "Z_T0"}


def test_partial_home_preserves_caller_order(rig):
    board, adapter = rig
    adapter.home(["Z_T0", "X"])  # runner sends tool-Z first (SRS D11)
    g28s = [c for c in board.command_log if c.startswith("G28")]
    assert g28s == ["G28 Z", "G28 X"]
    assert adapter.homed_axes() == {"X", "Z_T0"}


def test_absolute_move_emits_g90_and_mm_min_feed(rig):
    board, adapter = rig
    adapter.home()
    adapter.move_absolute({"X": 100.0, "Y": 50.0}, feed=10.0)
    assert "G90" in board.command_log
    move = [c for c in board.command_log if c.startswith("G1")][-1]
    assert "X100" in move and "Y50" in move
    assert move.endswith("F600")  # 10 mm/s -> 600 mm/min
    assert board.axes["X"]["machinePosition"] == pytest.approx(100.0)


def test_relative_move_wraps_g91_g90(rig):
    board, adapter = rig
    adapter.home()
    adapter.move_relative({"Z_T0": 5.0}, feed=5.0)
    idx = board.command_log.index("G91")
    assert board.command_log[idx + 1].startswith("G1 Z5")
    assert board.command_log[idx + 2] == "G90"
    assert board.axes["Z"]["machinePosition"] == pytest.approx(5.0)


def test_refused_move_surfaces_board_error(rig):
    board, adapter = rig  # not homed; RRF refuses plain G1
    with pytest.raises(DuetError, match="insufficient axes homed"):
        adapter.move_absolute({"X": 10.0}, feed=10.0)


def test_estop_halts_and_clear_reboots(rig):
    board, adapter = rig
    adapter.home()
    adapter.estop()
    assert board.halted
    assert adapter.get_status().emergency_active
    adapter.clear_estop()
    assert not board.halted
    # M999 is a reboot: datums are gone — re-home before moving
    assert adapter.homed_axes() == set()
    assert board.connect_calls >= 2


def test_stop_immediate_is_estop(rig):
    board, adapter = rig
    adapter.stop(StopMode.IMMEDIATE)
    assert board.halted


def test_full_status_shape_matches_ui_contract(rig):
    board, adapter = rig
    adapter.home()
    status = adapter.full_status()
    assert status["connected"] is True
    assert status["estop"] is False
    ax = status["axes"]["Z_T0"]
    assert set(ax) == {"position", "speed", "homed", "busy", "fault"}
    assert ax["homed"] is True


def test_soft_limits_and_max_speeds_come_from_profile(rig):
    _, adapter = rig
    assert adapter.soft_limits()["X"] == (0.0, 987.448)
    assert adapter.max_speeds()["Z_T0"] == 100.0


def test_build_machine_dispatches_duet():
    profile = make_duet_profile()
    machine = build_machine(profile)
    assert isinstance(machine.motion, DuetMotionAdapter)
    assert machine.motion_controller_id == "duet"


def test_home_failure_reported():
    board = ScriptedDuetBoard()

    class StubbornBoard(ScriptedDuetBoard):
        pass

    board = StubbornBoard()
    orig = board._execute

    def no_home(line):
        if line.startswith("G28"):
            board._busy_left = board.busy_reads
            return  # homing "runs" but never sets homed
        orig(line)

    board._execute = no_home
    profile = make_duet_profile()
    adapter = DuetMotionAdapter(DuetClient("fake", opener=board),
                                profile.axes_on("duet"))
    adapter.POLL_S = 0.01
    adapter.IDLE_GRACE_S = 0.05
    adapter.connect()
    with pytest.raises(DuetError, match="not homed"):
        adapter.home(["X"])

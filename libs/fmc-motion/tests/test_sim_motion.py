"""Basic motion behavior in sim mode (FR-MOVE-01/02, FR-SIM-02)."""

from __future__ import annotations

import pytest

from fmc_motion import MoveState


def test_context_manager_and_capabilities(sim_unhomed_ok):
    caps = sim_unhomed_ok.capabilities()
    assert caps["sim"] is True
    assert caps["axes"] == [0, 1]
    assert caps["continuous_jog"] is False


def test_move_absolute_blocking(sim_unhomed_ok):
    ctrl = sim_unhomed_ok
    handle = ctrl.move_absolute(0, 25.0)
    assert handle.state is MoveState.DONE
    assert ctrl.get_position(0) == pytest.approx(25.0, abs=0.01)


def test_move_relative(sim_unhomed_ok):
    ctrl = sim_unhomed_ok
    ctrl.move_absolute(0, 10.0)
    ctrl.move_relative(0, 5.0)
    assert ctrl.get_position(0) == pytest.approx(15.0, abs=0.01)
    ctrl.move_relative(0, -20.0)
    assert ctrl.get_position(0) == pytest.approx(-5.0, abs=0.01)


def test_nonblocking_move_and_wait(sim_unhomed_ok):
    ctrl = sim_unhomed_ok
    handle = ctrl.move_absolute(0, 40.0, wait=False)
    assert handle.wait(timeout=10.0)
    assert handle.state is MoveState.DONE
    assert ctrl.get_position(0) == pytest.approx(40.0, abs=0.01)


def test_status_reports_motion(sim_unhomed_ok):
    ctrl = sim_unhomed_ok
    handle = ctrl.move_absolute(0, 60.0, wait=False, speed=50.0)
    saw_busy = False
    saw_speed = 0.0
    while not handle.finished:
        status = ctrl.get_status()
        ax = status.axes[0]
        saw_busy = saw_busy or ax.busy
        saw_speed = max(saw_speed, ax.speed)
    handle.wait()
    assert saw_busy
    assert saw_speed > 0.0
    assert saw_speed <= 50.0 + 1e-6


def test_two_axes_move_concurrently(sim_unhomed_ok):
    ctrl = sim_unhomed_ok
    h0 = ctrl.move_absolute(0, 50.0, wait=False)
    h1 = ctrl.move_absolute(1, 30.0, wait=False)
    assert h0.wait(10.0) and h1.wait(10.0)
    assert ctrl.get_position(0) == pytest.approx(50.0, abs=0.01)
    assert ctrl.get_position(1) == pytest.approx(30.0, abs=0.01)


def test_per_move_speed_override_capped(sim_unhomed_ok):
    from fmc_motion import SoftLimitError

    with pytest.raises(SoftLimitError, match="speed"):
        sim_unhomed_ok.move_absolute(0, 10.0, speed=100000.0)


def test_configure_axis_validates_against_max(sim_unhomed_ok):
    from fmc_motion import SoftLimitError

    sim_unhomed_ok.configure_axis(0, speed=200.0)
    assert sim_unhomed_ok.get_axis_config(0).defaults.speed == 200.0
    with pytest.raises(SoftLimitError):
        sim_unhomed_ok.configure_axis(0, speed=301.0)


def test_command_history_recorded(sim_unhomed_ok):
    ctrl = sim_unhomed_ok
    ctrl.move_absolute(0, 5.0)
    ops = [h["op"] for h in ctrl.history]
    assert "connect" in ops and "move" in ops


def test_wait_for_axis(sim_unhomed_ok):
    ctrl = sim_unhomed_ok
    ctrl.move_absolute(0, 30.0, wait=False)
    assert ctrl.wait_for(0, timeout=10.0)
    assert ctrl.get_position(0) == pytest.approx(30.0, abs=0.01)

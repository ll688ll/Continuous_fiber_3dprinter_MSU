"""pause/resume of in-flight moves (vendor Pause_Run/Resume_Run, bench B4)."""

from __future__ import annotations

import time

import pytest

from fmc_motion import MotionError, MoveState


def _wait_running(handle, timeout=5.0):
    deadline = time.monotonic() + timeout
    while handle.state is MoveState.QUEUED and time.monotonic() < deadline:
        time.sleep(0.001)
    assert handle.state is MoveState.RUNNING


def test_pause_holds_then_resume_completes(sim_unhomed_ok):
    ctrl = sim_unhomed_ok
    handle = ctrl.move_absolute(0, 80.0, wait=False, speed=20.0)
    _wait_running(handle)
    time.sleep(0.02)
    ctrl.pause(0)
    status = ctrl.get_status().axes[0]
    assert status.paused and status.busy
    pos1 = ctrl.get_position(0)
    time.sleep(0.05)
    pos2 = ctrl.get_position(0)
    assert pos2 == pytest.approx(pos1, abs=0.01)  # held position
    assert not handle.finished                     # move is NOT complete
    ctrl.resume(0)
    assert handle.wait(20.0)
    assert ctrl.get_position(0) == pytest.approx(80.0, abs=0.02)
    assert not ctrl.get_status().axes[0].paused


def test_pause_idle_axis_is_noop(sim_unhomed_ok):
    sim_unhomed_ok.pause(0)  # no in-flight move: no-op, no error
    assert not sim_unhomed_ok.get_status().axes[0].paused


def test_pause_homing_refused(sim):
    ctrl = sim
    handle = ctrl.home(0, wait=False)
    _wait_running(handle)
    with pytest.raises(MotionError, match="cannot pause"):
        ctrl.pause(0)
    handle.wait(30.0)


def test_stop_overrides_pause(sim_unhomed_ok):
    from fmc_motion import MoveCancelledError

    ctrl = sim_unhomed_ok
    handle = ctrl.move_absolute(0, 80.0, wait=False, speed=20.0)
    _wait_running(handle)
    ctrl.pause(0)
    ctrl.stop(0, mode="immediate")
    with pytest.raises(MoveCancelledError):
        handle.wait(5.0)
    status = ctrl.get_status().axes[0]
    assert not status.paused
    # axis usable again
    ctrl.move_relative(0, 1.0)


def test_estop_overrides_pause_and_resume_blocked(sim_unhomed_ok):
    from fmc_motion import EStopActiveError, MoveCancelledError

    ctrl = sim_unhomed_ok
    handle = ctrl.move_absolute(0, 80.0, wait=False, speed=20.0)
    _wait_running(handle)
    ctrl.pause(0)
    ctrl.estop()
    with pytest.raises((MoveCancelledError, EStopActiveError)):
        handle.wait(5.0)
    with pytest.raises(EStopActiveError):
        ctrl.resume(0)
    ctrl.clear_estop()


def test_capabilities_report_pause(sim_unhomed_ok):
    assert sim_unhomed_ok.capabilities()["pause_resume"] is True

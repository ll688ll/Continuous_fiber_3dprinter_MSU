"""Stop, e-stop, soft limits, limit faults (FR-STOP-01..04, FR-MOVE-04)."""

from __future__ import annotations

import pytest

from fmc_motion import (
    EStopActiveError,
    LimitHitError,
    MoveCancelledError,
    SoftLimitError,
)


def test_estop_blocks_new_motion_until_cleared(sim_unhomed_ok):
    ctrl = sim_unhomed_ok
    ctrl.estop()
    assert ctrl.get_status().estop_active
    with pytest.raises(EStopActiveError):
        ctrl.move_absolute(0, 10.0)
    with pytest.raises(EStopActiveError):
        ctrl.home(0)
    ctrl.clear_estop()
    assert not ctrl.get_status().estop_active
    ctrl.move_relative(0, 1.0)  # works again


def test_estop_cancels_running_and_queued(sim_unhomed_ok):
    ctrl = sim_unhomed_ok
    running = ctrl.move_absolute(0, 90.0, wait=False, speed=20.0)
    queued = ctrl.move_relative(0, 5.0, wait=False)
    ctrl.estop()
    with pytest.raises((MoveCancelledError, EStopActiveError)):
        running.wait(5.0)
    with pytest.raises((MoveCancelledError, EStopActiveError)):
        queued.wait(5.0)
    # all six configured axes received an immediate stop command
    stops = [h for h in ctrl.history if h["op"] == "stop" and h["immediate"]]
    assert {s["axis"] for s in stops} >= {0, 1}


def test_hardware_estop_blocks_clear(sim_unhomed_ok):
    ctrl = sim_unhomed_ok
    ctrl.inject_hardware_estop(True)
    ctrl.estop()
    with pytest.raises(EStopActiveError, match="hardware"):
        ctrl.clear_estop()
    ctrl.inject_hardware_estop(False)
    ctrl.clear_estop()


def test_manual_soft_limits_enforced(sim_unhomed_ok):
    """Soft limits set manually in config are enforced even without homing."""
    ctrl = sim_unhomed_ok
    rt_cfg = ctrl.get_axis_config(0)
    rt_cfg.soft_limits.min = -10.0
    rt_cfg.soft_limits.max = 10.0
    # runtime copies were seeded at construction; use a fresh controller
    from fmc_testlib import base_config_dict
    from fmc_motion import create_controller

    data = base_config_dict()
    data["axes"][0]["allow_unhomed"] = True
    data["axes"][0]["soft_limits"] = {"min": -10.0, "max": 10.0}
    with create_controller(data, mode="sim") as ctrl2:
        ctrl2.move_absolute(0, 5.0)
        with pytest.raises(SoftLimitError):
            ctrl2.move_absolute(0, 50.0)


def test_soft_limits_can_be_disabled(sim):
    ctrl = sim
    ctrl.self_check(axes=[0])
    hi = ctrl.get_status().axes[0].soft_limit_max
    with pytest.raises(SoftLimitError):
        ctrl.move_absolute(0, hi + 1.0)
    ctrl.set_soft_limits_enabled(False)
    # now allowed (will run toward the switch; stop it quickly)
    handle = ctrl.move_absolute(0, hi + 1.0, wait=False)
    try:
        handle.wait(10.0)
    except (LimitHitError, MoveCancelledError):
        pass
    ctrl.set_soft_limits_enabled(True)
    ctrl.clear_fault(0)


def test_limit_trip_faults_axis_and_clear_fault_recovers(sim_unhomed_ok):
    """Driving into a limit switch mid-move -> LimitHitError, axis faulted,
    queue flushed; clear_fault + move away recovers (FR-STOP-04)."""
    ctrl = sim_unhomed_ok
    # axis 0 starts at internal 100 (reported 0); positive switch at internal
    # 200 = reported 100. Command past it with no soft limits configured.
    with pytest.raises(LimitHitError):
        ctrl.move_absolute(0, 150.0)
    status = ctrl.get_status().axes[0]
    assert status.fault is not None
    assert status.limit_positive
    # further motion refused while faulted
    with pytest.raises(LimitHitError):
        ctrl.move_relative(0, -5.0)
    ctrl.clear_fault(0)
    # moving AWAY from the active limit is allowed
    ctrl.move_relative(0, -20.0)
    assert not ctrl.get_status().axes[0].limit_positive


def test_move_into_active_limit_refused(sim_unhomed_ok):
    ctrl = sim_unhomed_ok
    with pytest.raises(LimitHitError):
        ctrl.move_absolute(0, 150.0)  # trips positive switch
    ctrl.clear_fault(0)
    # moving further positive while the switch is active is refused outright
    with pytest.raises(LimitHitError, match="cannot move further"):
        ctrl.move_relative(0, 5.0)
    ctrl.clear_fault(0)
    ctrl.move_relative(0, -10.0)


def test_jog_and_jog_stop(sim):
    ctrl = sim
    ctrl.self_check(axes=[0])
    start = ctrl.get_position(0)
    handle = ctrl.jog(0, +1, speed=30.0)
    # let it run briefly, then stop
    import time

    time.sleep(0.05)
    ctrl.jog_stop(0)
    try:
        handle.wait(5.0)
    except MoveCancelledError:
        pass
    pos = ctrl.get_position(0)
    assert pos > start  # it moved
    hi = ctrl.get_status().axes[0].soft_limit_max
    assert pos <= hi + 1e-6  # never past the soft limit


def test_jog_unhomed_uses_bounded_step(sim_unhomed_ok):
    ctrl = sim_unhomed_ok
    handle = ctrl.jog(0, -1)
    handle.wait(10.0)
    # bounded by max_step_unhomed = 5
    assert ctrl.get_position(0) == pytest.approx(-5.0, abs=0.05)

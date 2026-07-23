"""Per-axis FIFO queue behavior (FR-MOVE-03)."""

from __future__ import annotations

import pytest

from fmc_motion import MoveCancelledError, MoveState, SoftLimitError


def test_queued_moves_execute_in_order(sim_unhomed_ok):
    ctrl = sim_unhomed_ok
    h1 = ctrl.move_absolute(0, 20.0, wait=False)
    h2 = ctrl.move_relative(0, 10.0, wait=False)
    h3 = ctrl.move_relative(0, -5.0, wait=False)
    assert h3.wait(20.0)
    assert h1.state is MoveState.DONE
    assert h2.state is MoveState.DONE
    assert ctrl.get_position(0) == pytest.approx(25.0, abs=0.02)


def test_queue_depth_visible(sim_unhomed_ok):
    ctrl = sim_unhomed_ok
    ctrl.move_absolute(0, 80.0, wait=False, speed=20.0)  # long move
    ctrl.move_relative(0, 1.0, wait=False)
    ctrl.move_relative(0, 1.0, wait=False)
    queue = ctrl.get_queue(0)
    assert len(queue) >= 1  # first may already be running
    ctrl.wait_for(0, timeout=30.0)
    assert ctrl.get_queue(0) == []


def test_enqueue_validates_predicted_end_position(sim):
    """Soft-limit check applies to the queue's *predicted* end, not just the
    current position (FR-MOVE-03)."""
    ctrl = sim
    report = ctrl.self_check(axes=[0])
    assert report.passed
    status = ctrl.get_status().axes[0]
    lo, hi = status.soft_limit_min, status.soft_limit_max
    ctrl.move_absolute(0, hi - 1.0, wait=False)
    # second queued move pushes the predicted end past the soft max
    with pytest.raises(SoftLimitError):
        ctrl.move_relative(0, 5.0, wait=False)
    ctrl.wait_for(0, timeout=30.0)


def _wait_until_running(handle, timeout=5.0):
    import time

    deadline = time.monotonic() + timeout
    while handle.state is MoveState.QUEUED and time.monotonic() < deadline:
        time.sleep(0.001)
    assert handle.state is MoveState.RUNNING


def test_clear_queue_cancels_pending_not_running(sim_unhomed_ok):
    ctrl = sim_unhomed_ok
    running = ctrl.move_absolute(0, 90.0, wait=False, speed=20.0)
    _wait_until_running(running)
    queued = ctrl.move_relative(0, 5.0, wait=False)
    ctrl.clear_queue(0)
    with pytest.raises(MoveCancelledError):
        queued.wait(5.0)
    assert queued.state is MoveState.CANCELLED
    # the running move keeps going to completion
    assert running.wait(30.0)
    assert ctrl.get_position(0) == pytest.approx(90.0, abs=0.02)


def test_stop_flushes_queue_and_cancels_running(sim_unhomed_ok):
    ctrl = sim_unhomed_ok
    running = ctrl.move_absolute(0, 90.0, wait=False, speed=20.0)
    queued = ctrl.move_relative(0, 5.0, wait=False)
    ctrl.stop(0, mode="immediate")
    with pytest.raises(MoveCancelledError):
        running.wait(5.0)
    with pytest.raises(MoveCancelledError):
        queued.wait(5.0)
    # axis stopped well short of target
    assert ctrl.get_position(0) < 89.0
    # axis is usable again immediately
    ctrl.move_relative(0, 1.0)

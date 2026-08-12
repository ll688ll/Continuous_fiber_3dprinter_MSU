"""Coordinated 2-axis line moves (v1.1, FR-LINE-01..06)."""

from __future__ import annotations

import threading
import time

import pytest

from fmc_motion import (
    DeviceBusyError,
    EStopActiveError,
    LimitHitError,
    MotionError,
    MoveCancelledError,
    NotHomedError,
    SoftLimitError,
    create_controller,
)
from fmc_motion.interface import MoveState

from conftest import base_config_dict


@pytest.fixture
def sim_homed(sim):
    """Homed with discovered soft limits (axis 0: ~2..198, axis 1: ~2..148)."""
    assert sim.self_check().passed
    return sim


def test_line_reaches_both_targets(sim_homed):
    handle = sim_homed.move_line({0: 60.0, 1: 40.0})
    assert handle.state is MoveState.DONE
    status = sim_homed.get_status()
    assert status.axes[0].position == pytest.approx(60.0, abs=0.05)
    assert status.axes[1].position == pytest.approx(40.0, abs=0.05)
    assert handle.axes == (0, 1)


def test_line_axes_finish_together_and_path_is_straight(sim_homed):
    """Sample positions mid-move: both axes must stay on the segment
    (FR-LINE-05) and neither finishes long before the other."""
    start = {a: sim_homed.get_position(a) for a in (0, 1)}
    target = {0: 80.0, 1: 20.0}
    handle = sim_homed.move_line(target, speed=40.0, wait=False)

    samples = []
    while not handle.finished:
        snap = sim_homed.get_status()
        samples.append((snap.axes[0].position, snap.axes[1].position))
        time.sleep(0.002)
    handle.wait()

    dx = target[0] - start[0]
    dy = target[1] - start[1]
    moving = [s for s in samples
              if abs(s[0] - start[0]) > 1.0 and abs(s[0] - target[0]) > 1.0]
    assert moving, "expected to catch the move in flight"
    for x, y in moving:
        # colinearity: cross product of (p - start) with (target - start) ~ 0
        cross = (x - start[0]) * dy - (y - start[1]) * dx
        assert abs(cross) / max(abs(dx), abs(dy)) < 1.0  # mm deviation
        # progress fractions along each axis stay in lockstep
        fx = (x - start[0]) / dx
        fy = (y - start[1]) / dy
        assert fx == pytest.approx(fy, abs=0.05)


def test_line_records_history_and_composite_params(sim_homed):
    sim_homed.move_line({0: 30.0, 1: 30.0}, speed=50.0, accel=400.0, decel=400.0)
    ops = [h for h in sim_homed.history if h["op"] == "line"]
    assert len(ops) == 1
    assert ops[0]["targets"] == {0: 30.0, 1: 30.0}
    assert ops[0]["speed"] == 50.0


def test_line_requires_exactly_two_axes(sim_homed):
    with pytest.raises(MotionError, match="exactly 2"):
        sim_homed.move_line({0: 10.0})
    with pytest.raises(MotionError):
        sim_homed.move_line({0: 10.0, 1: 10.0, 2: 10.0})


def test_line_rejects_unhomed(sim):
    with pytest.raises(NotHomedError):
        sim.move_line({0: 10.0, 1: 10.0})


def test_line_soft_limits_checked_per_axis(sim_homed):
    # axis 1 travel is 150 mm; ask beyond its soft max
    with pytest.raises(SoftLimitError, match="axis 1"):
        sim_homed.move_line({0: 50.0, 1: 500.0})


def test_line_composite_speed_validated_against_min_of_maxima(sim_homed):
    # axis 0 max speed 300, axis 1 default max 300 -> min is 300
    with pytest.raises(SoftLimitError, match="composite speed"):
        sim_homed.move_line({0: 50.0, 1: 50.0}, speed=10_000.0)


def test_line_queues_behind_single_axis_moves(sim_homed):
    """A line waits for both axes: queue a slow single-axis move on axis 1,
    then a line; the line must not start until axis 1 finishes."""
    slow = sim_homed.move_relative(1, 30.0, speed=20.0, wait=False)
    line = sim_homed.move_line({0: 90.0, 1: 90.0}, wait=False)
    single_done_at = None
    while not line.finished:
        if slow.finished and single_done_at is None:
            single_done_at = time.monotonic()
        time.sleep(0.001)
    assert slow.finished  # dispatched strictly before the line completed
    line.wait()
    status = sim_homed.get_status()
    assert status.axes[0].position == pytest.approx(90.0, abs=0.05)
    assert status.axes[1].position == pytest.approx(90.0, abs=0.05)


def test_moves_queue_behind_line(sim_homed):
    line = sim_homed.move_line({0: 100.0, 1: 100.0}, speed=60.0, wait=False)
    after = sim_homed.move_absolute(0, 20.0, wait=False)
    after.wait()
    line.wait()
    # the single-axis move ran after the line: final X is 20
    assert sim_homed.get_position(0) == pytest.approx(20.0, abs=0.05)


def test_stop_on_either_axis_cancels_line(sim_homed):
    line = sim_homed.move_line({0: 150.0, 1: 120.0}, speed=30.0, wait=False)
    time.sleep(0.05)  # let it start
    sim_homed.stop(axis=1, mode="immediate")
    with pytest.raises(MoveCancelledError):
        line.wait(timeout=5.0)
    assert line.state is MoveState.CANCELLED
    sim_homed.wait_for(0, timeout=5.0)
    sim_homed.wait_for(1, timeout=5.0)
    assert sim_homed.get_position(0) < 149.0  # did not reach the target


def test_stop_cancels_queued_line_before_it_starts(sim_homed):
    slow = sim_homed.move_relative(0, 60.0, speed=20.0, wait=False)
    line = sim_homed.move_line({0: 90.0, 1: 90.0}, wait=False)
    sim_homed.stop(axis=0, mode="immediate")
    with pytest.raises(MoveCancelledError):
        line.wait(timeout=5.0)
    with pytest.raises(MoveCancelledError):
        slow.wait(timeout=5.0)
    sim_homed.wait_for(1, timeout=5.0)  # axis 1 worker released, not deadlocked


def test_estop_cancels_line_and_blocks_new_ones(sim_homed):
    line = sim_homed.move_line({0: 150.0, 1: 120.0}, speed=30.0, wait=False)
    time.sleep(0.05)
    sim_homed.estop()
    with pytest.raises((MoveCancelledError, EStopActiveError)):
        line.wait(timeout=5.0)
    with pytest.raises(EStopActiveError):
        sim_homed.move_line({0: 10.0, 1: 10.0})
    sim_homed.clear_estop()
    sim_homed.move_line({0: 10.0, 1: 10.0})  # works again


def test_limit_trip_mid_line_stops_both_and_faults_tripped_axis(sim_homed):
    line = sim_homed.move_line({0: 100.0, 1: 100.0}, speed=60.0, wait=False)
    time.sleep(0.03)
    sim_homed.inject_limit_stuck(1, "positive", True)
    with pytest.raises(LimitHitError, match="axis 1"):
        line.wait(timeout=5.0)
    sim_homed.clear_limit_stuck(1, "positive")
    sim_homed.wait_for(0, timeout=5.0)
    sim_homed.wait_for(1, timeout=5.0)
    status = sim_homed.get_status()
    assert status.axes[1].fault is not None
    assert status.axes[0].fault is None
    # motion on the faulted axis refused until cleared
    with pytest.raises(LimitHitError):
        sim_homed.move_line({0: 10.0, 1: 10.0})
    sim_homed.clear_fault(1)
    sim_homed.move_line({0: 10.0, 1: 10.0})


def test_line_busy_retry_after_queue_dispatch(sim_homed):
    """Back-to-back queued lines exercise the -10 retry path implicitly:
    they must all complete in order."""
    handles = [
        sim_homed.move_line({0: 40.0, 1: 40.0}, wait=False),
        sim_homed.move_line({0: 60.0, 1: 20.0}, wait=False),
        sim_homed.move_line({0: 10.0, 1: 10.0}, wait=False),
    ]
    for h in handles:
        h.wait(timeout=10.0)
    assert sim_homed.get_position(0) == pytest.approx(10.0, abs=0.05)
    assert sim_homed.get_position(1) == pytest.approx(10.0, abs=0.05)


def test_injected_busy_is_retried(sim_homed):
    sim_homed.inject_vendor_error(-10, count=2)
    handle = sim_homed.move_line({0: 25.0, 1: 25.0})
    assert handle.state is MoveState.DONE


def test_pause_on_line_raises(sim_homed):
    line = sim_homed.move_line({0: 150.0, 1: 120.0}, speed=20.0, wait=False)
    time.sleep(0.05)
    with pytest.raises(MotionError, match="cannot pause"):
        sim_homed.pause(0)
    sim_homed.stop()
    with pytest.raises(MoveCancelledError):
        line.wait(timeout=5.0)


def test_zero_length_line_completes_immediately(sim_homed):
    pos = {a: sim_homed.get_position(a) for a in (0, 1)}
    handle = sim_homed.move_line(pos)
    assert handle.state is MoveState.DONE


def test_capabilities_report_interpolation(sim):
    caps = sim.capabilities()
    assert caps["interpolation"] is True
    assert caps["line_axes"] == 2


def test_line_default_speed_is_min_of_axis_defaults():
    data = base_config_dict()
    data["axes"][1]["defaults"] = {"speed": 30.0, "accel": 800.0, "decel": 800.0}
    for ax in data["axes"]:
        ax["allow_unhomed"] = True
    ctrl = create_controller(data, mode="sim")
    ctrl.connect()
    try:
        ctrl.move_line({0: 5.0, 1: 5.0})
        op = [h for h in ctrl.history if h["op"] == "line"][0]
        assert op["speed"] == 30.0
    finally:
        ctrl.disconnect()


def test_concurrent_line_submissions_do_not_deadlock(sim_homed):
    """Two threads race line + single-axis submissions; everything resolves."""
    errors: list[Exception] = []

    def lines():
        try:
            for _ in range(3):
                sim_homed.move_line({0: 50.0, 1: 50.0}, wait=True, timeout=10.0)
        except Exception as exc:  # pragma: no cover - failure reporting
            errors.append(exc)

    def singles():
        try:
            for _ in range(3):
                sim_homed.move_absolute(0, 20.0, wait=True, timeout=10.0)
                sim_homed.move_absolute(1, 60.0, wait=True, timeout=10.0)
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    t1 = threading.Thread(target=lines)
    t2 = threading.Thread(target=singles)
    t1.start(); t2.start()
    t1.join(timeout=30.0); t2.join(timeout=30.0)
    assert not t1.is_alive() and not t2.is_alive(), "deadlock"
    assert not errors, errors
    assert sim_homed.wait_for(0, timeout=5.0)
    assert sim_homed.wait_for(1, timeout=5.0)

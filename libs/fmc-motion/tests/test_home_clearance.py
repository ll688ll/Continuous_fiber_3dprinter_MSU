"""Post-home switch clearance + silent-ignore detection (v1.1.1).

Lab findings 2026-08-12: the home switch stays engaged past its trip point
(hysteresis) and the controller SILENTLY IGNORES interpolated moves while a
limit switch is active — homing must clear the switch, and a "stopped" axis
that never reached its target must be an error, not a success.
"""

from __future__ import annotations

import pytest

from fmc_motion import MotionError, create_controller
from fmc_motion.interface import MoveState

from fmc_testlib import base_config_dict


def make_sim(clear_offset=0.0, homing_positive_axis=None):
    data = base_config_dict()
    for ax in data["axes"]:
        ax["homing"]["clear_offset_mm"] = clear_offset
    if homing_positive_axis is not None:
        data["axes"][homing_positive_axis]["homing"]["direction"] = "positive"
    ctrl = create_controller(data, mode="sim")
    ctrl.connect()
    return ctrl


def test_clearance_moves_off_the_switch_negative_homing():
    ctrl = make_sim(clear_offset=5.0)
    try:
        ctrl.home(0, wait=True)
        # sim frame: zero at switch trip, fall_step leaves 2.0 -> cleared to 5.0
        assert ctrl.get_position(0) == pytest.approx(5.0, abs=0.05)
        assert ctrl.is_homed(0)
    finally:
        ctrl.disconnect()


def test_clearance_moves_off_the_switch_positive_homing():
    ctrl = make_sim(clear_offset=5.0, homing_positive_axis=1)
    try:
        ctrl.home(1, wait=True)
        # homed to the positive switch: clearance goes NEGATIVE from it
        assert ctrl.get_position(1) == pytest.approx(-5.0, abs=0.05)
    finally:
        ctrl.disconnect()


def test_clearance_disabled_by_default():
    ctrl = make_sim(clear_offset=0.0)
    try:
        ctrl.home(0, wait=True)
        assert ctrl.get_position(0) == pytest.approx(2.0, abs=0.05)  # fall_step
    finally:
        ctrl.disconnect()


def test_clearance_skipped_when_already_clear():
    ctrl = make_sim(clear_offset=1.0)  # less than fall_step (2.0)
    try:
        ctrl.home(0, wait=True)
        assert ctrl.get_position(0) == pytest.approx(2.0, abs=0.05)  # untouched
    finally:
        ctrl.disconnect()


def test_self_check_still_passes_with_clearance():
    ctrl = make_sim(clear_offset=5.0)
    try:
        report = ctrl.self_check(axes=[0])
        assert report.passed, report.to_json()
    finally:
        ctrl.disconnect()


def test_negative_clear_offset_rejected():
    data = base_config_dict()
    data["axes"][0]["homing"]["clear_offset_mm"] = -1.0
    from fmc_motion import ConfigError
    with pytest.raises(ConfigError, match="clear_offset_mm"):
        create_controller(data, mode="sim")


# --------------------------------------------------- silent-ignore detection
@pytest.fixture
def sim_homed():
    ctrl = make_sim(clear_offset=5.0)
    ctrl.home(0, wait=True)
    ctrl.home(1, wait=True)
    yield ctrl
    ctrl.disconnect()


def test_ignored_single_axis_move_raises(sim_homed):
    sim_homed.inject_silent_ignore()
    with pytest.raises(MotionError, match="silently ignored"):
        sim_homed.move_absolute(0, 50.0, wait=True)
    # the axis is not faulted: the next (honest) move works
    handle = sim_homed.move_absolute(0, 50.0, wait=True)
    assert handle.state is MoveState.DONE


def test_ignored_line_move_raises(sim_homed):
    sim_homed.inject_silent_ignore()
    with pytest.raises(MotionError, match="silently ignored"):
        sim_homed.move_line({0: 50.0, 1: 40.0}, wait=True)
    handle = sim_homed.move_line({0: 50.0, 1: 40.0}, wait=True)
    assert handle.state is MoveState.DONE
    assert sim_homed.get_position(0) == pytest.approx(50.0, abs=0.05)


def test_ignored_move_flushes_queue(sim_homed):
    sim_homed.inject_silent_ignore()
    first = sim_homed.move_absolute(0, 50.0, wait=False)
    second = sim_homed.move_absolute(0, 60.0, wait=False)
    with pytest.raises(MotionError, match="silently ignored"):
        first.wait(timeout=10.0)
    with pytest.raises(Exception):  # cancelled by "previous command failed"
        second.wait(timeout=10.0)

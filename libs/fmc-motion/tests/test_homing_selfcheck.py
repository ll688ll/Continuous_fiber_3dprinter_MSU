"""Homing and self-check range discovery (FR-HOME-01..07)."""

from __future__ import annotations

import json

import pytest

from fmc_motion import NotHomedError, SelfCheckError


def test_move_unhomed_refused(sim):
    with pytest.raises(NotHomedError):
        sim.move_absolute(0, 10.0)


def test_home_sets_homed_and_position(sim):
    ctrl = sim
    assert not ctrl.is_homed(0)
    ctrl.home(0)
    assert ctrl.is_homed(0)
    # homed to negative switch, fall_step=2 -> parked at +2 in the homed frame
    assert ctrl.get_position(0) == pytest.approx(2.0, abs=0.05)


def test_home_all(sim):
    sim.home_all()
    assert sim.is_homed(0) and sim.is_homed(1)


def test_self_check_discovers_range(sim):
    ctrl = sim
    report = ctrl.self_check()
    assert report.passed
    ax0 = report.axis(0)
    # virtual travel is 200 mm; allow creep-poll overshoot up to overtravel
    assert ax0.span_mm == pytest.approx(200.0, abs=2.5)
    assert ax0.soft_limit_min == pytest.approx(2.0, abs=0.01)  # margin
    assert ax0.soft_limit_max == pytest.approx(ax0.span_mm - 2.0, abs=0.01)
    ax1 = report.axis(1)
    assert ax1.span_mm == pytest.approx(150.0, abs=2.5)
    # parked park_offset inside soft_min afterwards, clear of the home switch
    park_offset = ctrl.get_axis_config(0).self_check.park_offset_mm
    assert ctrl.get_position(0) == pytest.approx(ax0.soft_limit_min + park_offset,
                                                 abs=0.05)
    assert not ctrl.get_status().axes[0].limit_negative


def test_self_check_report_json(sim):
    report = sim.self_check(axes=[0])
    data = json.loads(report.to_json())
    assert data["axes"][0]["passed"] is True
    assert data["started_at"] and data["finished_at"]


def test_self_check_enforces_limits_afterwards(sim):
    from fmc_motion import SoftLimitError

    ctrl = sim
    ctrl.self_check(axes=[0])
    hi = ctrl.get_status().axes[0].soft_limit_max
    with pytest.raises(SoftLimitError):
        ctrl.move_absolute(0, hi + 10.0)
    # inside the range is fine
    ctrl.move_absolute(0, hi - 1.0)


def test_self_check_fails_when_both_limits_active(sim):
    ctrl = sim
    ctrl.inject_limit_stuck(0, "negative", True)
    ctrl.inject_limit_stuck(0, "positive", True)
    report = ctrl.self_check(axes=[0, 1], continue_on_error=True)
    assert not report.axis(0).passed
    assert "both limit switches" in report.axis(0).error
    # other axes still checked and pass (FR-HOME-05)
    assert report.axis(1).passed


def test_self_check_far_limit_never_trips(sim):
    """Stuck-off far switch -> axis stops without a trip -> SelfCheckError."""
    ctrl = sim
    ctrl.inject_limit_stuck(0, "positive", False)
    report = ctrl.self_check(axes=[0])
    assert not report.axis(0).passed
    assert "without the far limit switch triggering" in report.axis(0).error


def test_self_check_repeatable_with_existing_limits(sim):
    """Re-running self-check on an already-checked axis must work: the creep
    travels outside the previously discovered soft range by design, so limits
    are suspended during the check (regression: lab config with saved limits
    made every re-check fail with SoftLimitError)."""
    ctrl = sim
    first = ctrl.self_check(axes=[0])
    assert first.passed
    second = ctrl.self_check(axes=[0])
    assert second.passed, second.axis(0).error
    assert second.axis(0).span_mm == pytest.approx(first.axis(0).span_mm, abs=2.5)


def test_failed_self_check_keeps_previous_limits(sim):
    ctrl = sim
    assert ctrl.self_check(axes=[0]).passed
    before = ctrl.get_status().axes[0]
    ctrl.inject_limit_stuck(0, "positive", False)  # break the far switch
    report = ctrl.self_check(axes=[0])
    assert not report.axis(0).passed
    after = ctrl.get_status().axes[0]
    assert after.soft_limit_min == before.soft_limit_min
    assert after.soft_limit_max == before.soft_limit_max


def test_self_check_refused_when_estopped(sim):
    ctrl = sim
    ctrl.estop()
    report = ctrl.self_check(axes=[0])
    assert not report.axis(0).passed
    assert "e-stop" in report.axis(0).error.lower()

"""Fault injection paths (FR-SIM-04) and watchdog (MoveTimeoutError)."""

from __future__ import annotations

import pytest

from fmc_motion import (
    ControllerConnectionError,
    HardwareFaultError,
    MoveTimeoutError,
    create_controller,
)
from conftest import base_config_dict


def test_connect_failure():
    ctrl = create_controller(base_config_dict(), mode="sim")
    ctrl.inject_connect_failure(-1)
    with pytest.raises(ControllerConnectionError) as excinfo:
        ctrl.connect()
    assert excinfo.value.vendor_code == -1
    # second attempt succeeds (FR-CON-03)
    ctrl.connect()
    ctrl.disconnect()


def test_vendor_error_surfaces_with_code(sim_unhomed_ok):
    ctrl = sim_unhomed_ok
    ctrl.inject_vendor_error(-5)
    with pytest.raises(HardwareFaultError) as excinfo:
        ctrl.move_absolute(0, 10.0)
    assert excinfo.value.vendor_code == -5
    # axis recovers for the next command
    ctrl.move_absolute(0, 10.0)


def test_move_stall_hits_watchdog():
    data = base_config_dict()
    for ax in data["axes"]:
        ax["allow_unhomed"] = True
        ax["move_timeout_s"] = 0.3  # real seconds
    ctrl = create_controller(data, mode="sim")
    ctrl.connect()
    try:
        ctrl.inject_move_stall(0)
        with pytest.raises(MoveTimeoutError):
            ctrl.move_absolute(0, 10.0, timeout=5.0)
        assert ctrl.get_status().axes[0].fault is not None
        ctrl.inject_move_stall(0, False)
        ctrl.clear_fault(0)
        ctrl.move_relative(0, 1.0)
    finally:
        ctrl.disconnect()


def test_polling_delivers_status(sim_unhomed_ok):
    import time

    ctrl = sim_unhomed_ok
    seen = []
    ctrl.start_polling(200.0, seen.append)
    ctrl.move_absolute(0, 20.0)
    time.sleep(0.05)
    ctrl.stop_polling()
    assert seen, "polling callback never fired"
    assert seen[-1].connected

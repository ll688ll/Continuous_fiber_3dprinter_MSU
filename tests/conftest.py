"""Shared fixtures: a 3-axis sim machine and a matching printer profile."""

from __future__ import annotations

import copy

import pytest

from printer_testlib import FMC_SIM_CONFIG, make_profile


@pytest.fixture
def profile():
    return make_profile()


@pytest.fixture
def adapter(profile):
    fmc_motion = pytest.importorskip("fmc_motion")
    from printer_msu.controllers.motion.fmc_motion_adapter import FmcMotionAdapter

    ctrl = fmc_motion.create_controller(copy.deepcopy(FMC_SIM_CONFIG), mode="sim")
    adp = FmcMotionAdapter(ctrl, axis_map=profile.motion.axis_map)
    adp.connect()
    yield adp
    adp.disconnect()

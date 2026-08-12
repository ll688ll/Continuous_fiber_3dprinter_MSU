from __future__ import annotations

import pytest

from fmc_motion import ControllerConfig, SimMotionController, create_controller

from fmc_testlib import base_config_dict




@pytest.fixture
def config() -> ControllerConfig:
    return ControllerConfig.from_dict(base_config_dict())


@pytest.fixture
def sim(config) -> SimMotionController:
    ctrl = create_controller(config, mode="sim")
    ctrl.connect()
    yield ctrl
    ctrl.disconnect()


@pytest.fixture
def sim_unhomed_ok() -> SimMotionController:
    """Sim controller whose axes allow motion without homing."""
    data = base_config_dict()
    for ax in data["axes"]:
        ax["allow_unhomed"] = True
    ctrl = create_controller(data, mode="sim")
    ctrl.connect()
    yield ctrl
    ctrl.disconnect()

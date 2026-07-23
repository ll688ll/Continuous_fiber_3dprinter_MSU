from __future__ import annotations

import pytest

from fmc_motion import ControllerConfig, SimMotionController, create_controller


def base_config_dict(**overrides) -> dict:
    """Two-axis sim config tuned for fast tests.

    time_scale 50 + poll 2 ms keeps moves in the millisecond range while the
    poll granularity (0.1 sim-s per real poll) stays small relative to travel.
    """
    cfg = {
        "connection": {"device_id": 1, "ip": "192.168.0.30", "port": 8088},
        "poll_interval_s": 0.002,
        "axes": [
            {
                "index": 0,
                "name": "X",
                "homing": {"direction": "negative", "speed": 40.0, "accdec": 400.0,
                           "fall_step": 2.0},
                "defaults": {"speed": 100.0, "accel": 800.0, "decel": 800.0},
                "max": {"speed": 300.0, "accel": 2000.0, "decel": 2000.0},
                "self_check": {"creep_speed": 15.0, "max_search_mm": 400.0,
                               "margin_mm": 2.0},
                "jog": {"speed": 30.0, "max_step_unhomed": 5.0},
                "allow_unhomed": False,
            },
            {
                "index": 1,
                "name": "Y",
                "homing": {"direction": "negative", "speed": 40.0, "accdec": 400.0,
                           "fall_step": 2.0},
                "defaults": {"speed": 100.0, "accel": 800.0, "decel": 800.0},
                "self_check": {"creep_speed": 15.0, "max_search_mm": 400.0,
                               "margin_mm": 2.0},
                "allow_unhomed": False,
            },
        ],
        "sim": {
            "time_scale": 50.0,
            "axes": {
                "0": {"travel_mm": 200.0, "start_pos_mm": 100.0, "overtravel_mm": 2.0},
                "1": {"travel_mm": 150.0, "start_pos_mm": 75.0, "overtravel_mm": 2.0},
            },
        },
    }
    cfg.update(overrides)
    return cfg


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

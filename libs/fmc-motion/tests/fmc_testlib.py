"""Shared config builder for the fmc_motion test suite.

Lives outside conftest.py so `from fmc_testlib import ...` is unambiguous
when this suite is collected together with the printer's tests/ directory
(two modules literally named `conftest` would collide in sys.modules).
"""

from __future__ import annotations


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

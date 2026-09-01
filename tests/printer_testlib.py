"""Shared config/profile builders for the printer test suite.

Lives outside conftest.py so `from printer_testlib import ...` is unambiguous
when this suite is collected together with libs/fmc-motion/tests (two modules
literally named `conftest` would collide in sys.modules).
"""

from __future__ import annotations

import copy

from printer_msu.config.profile import MachineProfile, profile_from_dict

#: fmc_motion sim config — X/Y/Z_T0 on indices 0/1/2, fast for tests
FMC_SIM_CONFIG = {
    "connection": {"device_id": 1, "ip": "192.168.0.30", "port": 8088},
    "poll_interval_s": 0.002,
    "axes": [
        {"index": 0, "name": "X",
         "homing": {"direction": "negative", "speed": 40, "accdec": 400,
                    "fall_step": 2},
         "defaults": {"speed": 100, "accel": 800, "decel": 800},
         "max": {"speed": 200, "accel": 2000, "decel": 2000}},
        {"index": 1, "name": "Y",
         "homing": {"direction": "negative", "speed": 40, "accdec": 400,
                    "fall_step": 2},
         "defaults": {"speed": 100, "accel": 800, "decel": 800},
         "max": {"speed": 200, "accel": 2000, "decel": 2000}},
        {"index": 2, "name": "Z_T0",
         "homing": {"direction": "negative", "speed": 30, "accdec": 300,
                    "fall_step": 2},
         "defaults": {"speed": 50, "accel": 500, "decel": 500},
         "max": {"speed": 100, "accel": 1000, "decel": 1000}},
    ],
    "sim": {"time_scale": 50.0,
            "axes": {"0": {"travel_mm": 200.0},
                     "1": {"travel_mm": 150.0},
                     "2": {"travel_mm": 100.0}}},
}

PROFILE_DATA = {
    "profile_name": "test sim printer",
    "motion": {
        "config": "unused-in-unit-tests.json",
        "mode": "sim",
        "axis_map": {"X": 0, "Y": 1, "Z_T0": 2},
    },
    "tools": [{"tool_index": 0, "z_axis": "Z_T0"}],
    "gcode": {"default_tool": 0, "default_feed_mm_min": 1200.0,
              "travel_feed_mm_min": 3000.0},
    "policies": {},
}


def make_profile(**overrides) -> MachineProfile:
    data = copy.deepcopy(PROFILE_DATA)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(data.get(key), dict):
            data[key].update(value)
        else:
            data[key] = value
    return profile_from_dict(data)


# ------------------------------------------------------------ duet profiles
DUET_PROFILE_DATA = {
    "schema_version": 2,
    "profile_name": "duet test machine",
    "controllers": [
        {"id": "duet", "type": "duet",
         "connection": {"host": "192.168.0.40"}},
    ],
    "axes": [
        {"name": "X", "controller": "duet", "channel": 0, "letter": "X",
         "steps_per_mm": 142.857, "max_speed_mm_s": 200.0,
         "max_accel_mm_s2": 1000.0, "jerk_mm_s": 8.0,
         "limits_mm": [0.0, 987.448],
         "endstop": {"end": "min", "pin": "io3.in", "inverted": True}},
        {"name": "Y", "controller": "duet", "channel": 1, "letter": "Y",
         "steps_per_mm": 142.857, "max_speed_mm_s": 200.0,
         "max_accel_mm_s2": 1000.0, "jerk_mm_s": 8.0,
         "limits_mm": [0.0, 290.269],
         "endstop": {"end": "min", "pin": "io4.in", "inverted": True}},
        {"name": "Z_T0", "controller": "duet", "channel": 2, "letter": "Z",
         "steps_per_mm": 500.0, "max_speed_mm_s": 100.0,
         "max_accel_mm_s2": 800.0, "jerk_mm_s": 2.0,
         "limits_mm": [0.0, 282.306], "homes_up": True,
         "endstop": {"end": "min", "pin": "io5.in", "inverted": True}},
    ],
    "tools": [{"tool_index": 0, "z_axis": "Z_T0", "plane_offset_mm": 100.0}],
    "gcode": {"default_tool": 0, "default_feed_mm_min": 1200.0,
              "travel_feed_mm_min": 2400.0},
    "workspace": {"xy_offset": {"X": 30.0, "Y": 30.0}, "z_mode": "bed"},
    "policies": {},
}


def make_duet_profile(**overrides) -> MachineProfile:
    data = copy.deepcopy(DUET_PROFILE_DATA)
    for key, value in overrides.items():
        data[key] = value
    return profile_from_dict(data)

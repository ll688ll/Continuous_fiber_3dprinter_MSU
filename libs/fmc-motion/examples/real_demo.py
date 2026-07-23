"""Real-hardware demo (Windows + FMC006GE31 on the network).

SAFETY: this moves real actuators. Check that axis 0's travel is clear and the
limit switches are wired before running. Start with a single axis.

Run:  python libs/fmc-motion/examples/real_demo.py
"""

from __future__ import annotations

import logging

from fmc_motion import create_controller

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")

CONFIG = {
    "connection": {"device_id": 1, "ip": "192.168.0.30", "port": 8088},
    "poll_interval_s": 0.05,
    "axes": [
        {
            "index": 0,
            "name": "X",
            "homing": {"direction": "negative", "speed": 20, "accdec": 200,
                       "fall_step": 2},
            "defaults": {"speed": 50, "accel": 300, "decel": 300},
            "max": {"speed": 200, "accel": 1000, "decel": 1000},
            "self_check": {"creep_speed": 5, "max_search_mm": 500, "margin_mm": 2},
        },
    ],
}


def main() -> None:
    with create_controller(CONFIG, mode="real") as ctrl:
        print("connected:", ctrl.capabilities())

        report = ctrl.self_check(axes=[0])
        print(report.to_json())
        if not report.passed:
            print("self-check FAILED - not moving")
            return

        ctrl.move_absolute(0, 20.0)
        print("position:", ctrl.get_position(0))
        ctrl.move_relative(0, -10.0)
        print("position:", ctrl.get_position(0))


if __name__ == "__main__":
    main()

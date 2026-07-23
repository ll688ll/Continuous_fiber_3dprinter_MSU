"""Sim-mode demo: full self-check + moves, no hardware required (any OS).

Run:  python libs/fmc-motion/examples/sim_demo.py
"""

from __future__ import annotations

import logging

from fmc_motion import create_controller

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")

CONFIG = {
    "connection": {"device_id": 1, "ip": "192.168.0.30", "port": 8088},
    "axes": [
        {"index": 0, "name": "X",
         "homing": {"direction": "negative", "speed": 20, "fall_step": 2},
         "defaults": {"speed": 100, "accel": 500, "decel": 500}},
        {"index": 1, "name": "Y",
         "homing": {"direction": "negative", "speed": 20, "fall_step": 2},
         "defaults": {"speed": 100, "accel": 500, "decel": 500}},
    ],
    "sim": {
        "time_scale": 20.0,  # run 20x faster than real time
        "axes": {"0": {"travel_mm": 200}, "1": {"travel_mm": 150}},
    },
}


def main() -> None:
    with create_controller(CONFIG, mode="sim") as ctrl:
        print("capabilities:", ctrl.capabilities())

        report = ctrl.self_check()
        print(report.to_json())

        ctrl.configure_axis(0, speed=150)
        ctrl.move_absolute(0, 50.0)
        ctrl.move_absolute(1, 25.0)
        h = ctrl.move_relative(0, 30.0, wait=False)  # async
        h.wait()

        status = ctrl.get_status()
        for idx, ax in status.axes.items():
            print(f"axis {idx} ({ax.name}): pos={ax.position:.2f} mm, "
                  f"range=[{ax.soft_limit_min:.1f}, {ax.soft_limit_max:.1f}]")


if __name__ == "__main__":
    main()

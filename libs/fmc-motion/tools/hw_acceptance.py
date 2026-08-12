"""Library-level hardware acceptance run (PRD section 7, criterion 2).

Sequence per axis, through the fmc_motion public API (mode="real"):
  connect -> self_check -> configured moves -> queued moves -> jog + stop
  -> e-stop (dispatch time measured) -> recover -> park

Usage (lab machine):
  python libs/fmc-motion/tools/hw_acceptance.py --config machine.json --axes 0
  # --sim runs the identical sequence against the sim backend as a dry run

The run writes hw_acceptance_report.json; attach it to the PRD log.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fmc_motion import (  # noqa: E402
    MoveCancelledError,
    create_controller,
)

log = logging.getLogger("acceptance")

AXIS_NAMES = ("X", "Y", "Z", "X1", "Y1", "Z1")


def default_config(axes: list[int], ip: str) -> dict:
    """Built-in template covering exactly the requested axes.

    poll_interval_s 0.25 comes from bench B3: one status round-trip is ~120 ms.
    """
    return {
        "connection": {"device_id": 1, "ip": ip, "port": 8088},
        "poll_interval_s": 0.25,
        "axes": [
            {
                "index": i,
                "name": AXIS_NAMES[i],
                "homing": {"direction": "negative", "speed": 20, "accdec": 200,
                           "fall_step": 2},
                "defaults": {"speed": 50, "accel": 300, "decel": 300},
                "max": {"speed": 200, "accel": 1000, "decel": 1000},
                "self_check": {"creep_speed": 5, "max_search_mm": 500,
                               "margin_mm": 2},
                "jog": {"speed": 20, "max_step_unhomed": 5},
            }
            for i in axes
        ],
        "sim": {"time_scale": 10.0,
                "axes": {str(i): {"travel_mm": 200} for i in axes}},
    }


def run(ctrl, axes: list[int], report: dict) -> None:
    # 1. self-check: homing + range discovery
    log.info("STEP 1: self_check on axes %s", axes)
    sc = ctrl.self_check(axes=axes)
    report["self_check"] = json.loads(sc.to_json())
    if not sc.passed:
        raise SystemExit("self-check failed - aborting acceptance run")

    for axis in axes:
        status = ctrl.get_status().axes[axis]
        lo, hi = status.soft_limit_min, status.soft_limit_max
        mid = (lo + hi) / 2
        quarter = lo + (hi - lo) / 4

        # 2. configured moves at explicit speed/accel/decel
        log.info("STEP 2: moves on axis %s (range %.1f..%.1f)", axis, lo, hi)
        ctrl.move_absolute(axis, mid, speed=40, accel=300, decel=300)
        ctrl.move_relative(axis, -10.0)
        pos = ctrl.get_position(axis)
        report.setdefault("moves", {})[axis] = {
            "target": mid - 10.0, "reached": pos,
            "error_mm": round(abs(pos - (mid - 10.0)), 4),
        }

        # 3. queued moves (FIFO)
        log.info("STEP 3: queued moves on axis %s", axis)
        h1 = ctrl.move_absolute(axis, quarter, wait=False)
        h2 = ctrl.move_relative(axis, 5.0, wait=False)
        h3 = ctrl.move_relative(axis, 5.0, wait=False)
        h3.wait(120.0)
        report.setdefault("queue", {})[axis] = {
            "all_done": all(h.state.value == "done" for h in (h1, h2, h3)),
            "final_pos": ctrl.get_position(axis),
        }

        # 4. jog + jog_stop
        log.info("STEP 4: jog on axis %s", axis)
        start = ctrl.get_position(axis)
        jh = ctrl.jog(axis, +1, speed=15)
        time.sleep(1.0)
        ctrl.jog_stop(axis)
        try:
            jh.wait(30.0)
        except MoveCancelledError:
            pass
        report.setdefault("jog", {})[axis] = {
            "moved_mm": round(ctrl.get_position(axis) - start, 3),
            "within_soft_limit": ctrl.get_position(axis) <= hi + 1e-6,
        }

    # 5. e-stop timing (NFR-02): during motion on the first axis
    axis = axes[0]
    log.info("STEP 5: e-stop during motion")
    ax_status = ctrl.get_status().axes[axis]
    ctrl.move_absolute(axis, (ax_status.soft_limit_min + ax_status.soft_limit_max) / 2)
    ctrl.move_relative(axis, 15.0, wait=False, speed=10)
    time.sleep(0.5)
    t0 = time.perf_counter()
    ctrl.estop()
    estop_dispatch_ms = (time.perf_counter() - t0) * 1000.0
    stopped_pos = ctrl.get_position(axis)
    time.sleep(0.5)
    report["estop"] = {
        "dispatch_all_axes_ms": round(estop_dispatch_ms, 2),
        "held_position": abs(ctrl.get_position(axis) - stopped_pos) < 0.05,
        "target_ms": 500.0,
        "pass": estop_dispatch_ms <= 500.0,
    }
    log.info("  e-stop dispatch: %.1f ms", estop_dispatch_ms)

    # 6. recover and park
    log.info("STEP 6: recover")
    ctrl.clear_estop()
    for axis in axes:
        status = ctrl.get_status().axes[axis]
        ctrl.move_absolute(axis, status.soft_limit_min)
    report["recovered"] = True


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="machine config JSON/YAML (default: built-in template for --axes)")
    ap.add_argument("--axes", default="0", help="comma-separated axis indices")
    ap.add_argument("--ip", default="192.168.0.30")
    ap.add_argument("--sim", action="store_true", help="dry-run in sim mode")
    ap.add_argument("--out", default="hw_acceptance_report.json")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s")
    axes = [int(a) for a in args.axes.split(",")]
    config = args.config if args.config else default_config(axes, args.ip)
    mode = "sim" if args.sim else "real"

    report: dict = {"mode": mode, "axes": axes,
                    "started": time.strftime("%Y-%m-%d %H:%M:%S")}
    ok = False
    with create_controller(config, mode=mode) as ctrl:
        try:
            run(ctrl, axes, report)
            ok = True
        finally:
            report["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
            report["passed"] = ok and report.get("estop", {}).get("pass", False)
            Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(f"\nreport written to {args.out}")
            print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

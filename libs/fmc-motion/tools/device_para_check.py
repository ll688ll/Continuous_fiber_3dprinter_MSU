"""Dump controller device parameters and check them against your machine.

Read-only (Get_Device_Para); this tool never writes parameters — change them
in the vendor's own application if needed.

Checks performed per axis (see PRD "Device parameters" notes):
  1. controller-side soft limit vs. the travel your self-check discovered —
     a long actuator on a channel with a tight controller limit (defaults:
     X +/-2000 mm, all other channels +/-200 mm!) gets clamped by the
     controller before it ever reaches its far switch;
  2. homing duration at your configured homing speed vs. the controller's
     Home Timeout (default 10000) — homing a long axis from the far end can
     exceed it;
  3. prints pulses/mm so unit scaling can be sanity-checked against a
     physical ruler measurement (lead assumes the installed screw!).

Usage (lab machine):
  python libs/fmc-motion/tools/device_para_check.py --ip 192.168.0.30
  python libs/fmc-motion/tools/device_para_check.py --config machine.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fmc_motion import ControllerConfig  # noqa: E402
from fmc_motion.fmc006ge31 import FMC006GE31Controller  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="library machine config (JSON/YAML) to check against")
    ap.add_argument("--ip", default="192.168.0.30")
    ap.add_argument("--port", type=int, default=8088)
    ap.add_argument("--out", default="device_para.json")
    args = ap.parse_args()

    if args.config:
        cfg = ControllerConfig.load(args.config)
    else:
        # minimal config: axis definitions only needed for the cross-checks
        cfg = ControllerConfig.from_dict({
            "connection": {"device_id": 1, "ip": args.ip, "port": args.port},
            "poll_interval_s": 0.25,
            "axes": [{"index": 0, "name": "X"}],
        })

    ctrl = FMC006GE31Controller(cfg)
    ctrl.connect()
    try:
        para = ctrl.read_device_parameters()
    finally:
        ctrl.disconnect()

    print(json.dumps(para, indent=2))
    Path(args.out).write_text(json.dumps(para, indent=2), encoding="utf-8")
    print(f"\nwritten to {args.out}\n")

    warnings: list[str] = []
    for ax_cfg in cfg.axes:
        i = ax_cfg.index
        dev = para["axes"][i]
        name = ax_cfg.name or str(i)

        # 1. controller soft limit vs discovered/needed travel
        travel = ax_cfg.soft_limits.span_mm or ax_cfg.soft_limits.max
        ctrl_limit = dev["soft_limit_positive_mm"]
        if travel and ctrl_limit and travel > ctrl_limit * 0.9:
            warnings.append(
                f"axis {i} ({name}): travel {travel:.0f} mm is at/over 90% of the "
                f"controller's own soft limit ({ctrl_limit} mm) - the controller "
                f"will clamp motion before the far switch; raise the limit in "
                f"the vendor tool"
            )

        # 2. homing duration vs controller home timeout (assume ms)
        if travel and ax_cfg.homing.speed:
            worst_home_s = travel / ax_cfg.homing.speed
            timeout_s = dev["home_timeout"] / 1000.0
            if worst_home_s > timeout_s:
                warnings.append(
                    f"axis {i} ({name}): homing from the far end takes "
                    f"~{worst_home_s:.0f} s at {ax_cfg.homing.speed} mm/s but the "
                    f"controller Home Timeout is {timeout_s:.0f} s - raise the "
                    f"timeout in the vendor tool or increase homing speed"
                )

        # 3. scaling info
        print(f"axis {i} ({name}): {dev['pulses_per_mm']:.0f} pulses/mm "
              f"(lead {dev['lead_mm_per_rev']} mm, {dev['div_pulses_per_rev']} "
              f"pulses/rev) - verify with a ruler if this actuator's screw "
              f"lead differs")

    print()
    if warnings:
        print("WARNINGS:")
        for w in warnings:
            print(f"  ! {w}")
    else:
        print("no conflicts between controller parameters and library config")


if __name__ == "__main__":
    main()

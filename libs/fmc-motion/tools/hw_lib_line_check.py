"""Diagnose silent no-motion of library line moves after homing (lab issue
2026-08-12).

Observed: a real print "completed" with every Line_2Axis dispatched rc=0 but
ZERO motion — each segment finished in exactly the grace+settle floor
(~2.04 s) and positions never changed. The raw bench (hw_line_bench.py) DID
move, but it jogged single-axis to (20, 20) before its first line, while a
print fires its first line straight from the post-home position at the home
switch — which is known to stay engaged ~2.5 mm past its trip point
(hysteresis; see the self-check park_offset fix).

This script reproduces through the LIBRARY (same path the printer uses),
step by step, printing positions and limit-switch bits, to answer:

  A. are the negative limit switches still ACTIVE at the post-home position?
  B. does a 2-axis line move fail from the post-home position?
  C. do single-axis moves work from there (they should — homing fall-back
     and self-check rely on that)?
  D. does the same line move work after the axes are clear of the switches?

Usage (lab PC; also runs with --mode sim as a dry run):
  python libs/fmc-motion/tools/hw_lib_line_check.py --config configs/msu_lab.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "src"))

from fmc_motion import create_controller  # noqa: E402

TOLERANCE_MM = 0.5
RESULTS: dict[str, object] = {}


def snapshot(ctrl, axes) -> dict:
    status = ctrl.get_status()
    out = {}
    for a in axes:
        ax = status.axes[a]
        out[a] = {"pos": round(ax.position, 3),
                  "limit_neg": ax.limit_negative, "limit_pos": ax.limit_positive}
    return out


def show(label: str, snap: dict) -> None:
    parts = [f"axis {a}: {v['pos']} mm"
             f"{' [NEG-LIMIT ACTIVE]' if v['limit_neg'] else ''}"
             f"{' [POS-LIMIT ACTIVE]' if v['limit_pos'] else ''}"
             for a, v in snap.items()]
    print(f"  {label}: " + "; ".join(parts))


def reached(snap: dict, targets: dict) -> bool:
    return all(abs(snap[a]["pos"] - t) <= TOLERANCE_MM for a, t in targets.items())


def try_line(ctrl, axes, targets, speed, key: str) -> bool:
    print(f"\n--- {key}: move_line -> {targets} at {speed} mm/s ---")
    t0 = time.monotonic()
    try:
        ctrl.move_line(targets, speed=speed, wait=True, timeout=120.0)
        error = None
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        print(f"  raised: {error}")
    duration = round(time.monotonic() - t0, 2)
    snap = snapshot(ctrl, axes)
    show("after", snap)
    ok = error is None and reached(snap, targets)
    RESULTS[key] = {"targets": targets, "duration_s": duration, "error": error,
                    "final": {a: v["pos"] for a, v in snap.items()},
                    "moved_to_target": ok}
    print(f"  -> {'MOVED to target' if ok else 'DID NOT reach target'} "
          f"({duration} s)")
    return ok


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="configs/msu_lab.json")
    ap.add_argument("--mode", default="real", choices=["real", "sim"])
    ap.add_argument("--axis-a", type=int, default=0)
    ap.add_argument("--axis-b", type=int, default=1)
    ap.add_argument("--speed", type=float, default=20.0)
    ap.add_argument("--out", default="lib_line_check_results.json")
    args = ap.parse_args()
    axes = (args.axis_a, args.axis_b)

    ctrl = create_controller(args.config, mode=args.mode)
    ctrl.connect()
    try:
        print("=== step 1: home both axes (library path, same as a print) ===")
        for a in axes:
            ctrl.home(a, wait=True)
        post_home = snapshot(ctrl, axes)
        show("post-home", post_home)
        RESULTS["post_home"] = post_home
        if any(v["limit_neg"] for v in post_home.values()):
            print("  NOTE: negative limit switch still ACTIVE at the post-home "
                  "position (hysteresis) — prime suspect.")

        # B: line straight from post-home (exactly what a print does first)
        line_from_home = try_line(ctrl, axes, {axes[0]: 10.0, axes[1]: 10.0},
                                  args.speed, "line_from_post_home")

        # C: single-axis moves away from the switches (library Jog path)
        print("\n--- single_axis_clear: move_absolute to 15/15 ---")
        ctrl.move_absolute(axes[0], 15.0, wait=True)
        ctrl.move_absolute(axes[1], 15.0, wait=True)
        cleared = snapshot(ctrl, axes)
        show("after", cleared)
        singles_ok = reached(cleared, {axes[0]: 15.0, axes[1]: 15.0})
        RESULTS["single_axis_clear"] = {
            "final": {a: v["pos"] for a, v in cleared.items()},
            "moved_to_target": singles_ok}
        print(f"  -> {'MOVED' if singles_ok else 'DID NOT MOVE'}")

        # D: the same line, now clear of the switches
        line_when_clear = try_line(ctrl, axes, {axes[0]: 40.0, axes[1]: 30.0},
                                   args.speed, "line_when_clear")

        print("\n=== verdict ===")
        if not line_from_home and line_when_clear:
            verdict = ("CONFIRMED: the controller silently ignores Line_2Axis "
                       "at/near the home switch; a post-home clearance move "
                       "fixes it")
        elif line_from_home and line_when_clear:
            verdict = ("line moves worked this time — the print failure was "
                       "something else or intermittent; rerun / capture logs")
        elif not line_when_clear and singles_ok:
            verdict = ("line moves fail even when clear of switches — "
                       "library real-mode line dispatch is broken; compare "
                       "against hw_line_bench.py raw parameters")
        else:
            verdict = "single-axis moves failed too — check connection/estop"
        RESULTS["verdict"] = verdict
        print(f"  {verdict}")
    finally:
        ctrl.disconnect()

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(RESULTS, f, indent=2)
    print(f"\nresults written to {args.out}")


if __name__ == "__main__":
    main()

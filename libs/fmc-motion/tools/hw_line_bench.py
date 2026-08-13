"""Hardware bench tests B-L1..B-L4 for Line_2Axis (printer PRD FR-LINE-07).

Answers the vendor-manual gaps for 2-axis line interpolation directly against
the raw DLL, in the style of hw_bench.py. Each phase asks for confirmation
before moving anything.

The B-L1 test geometry is chosen so that EVERY plausible interpretation of the
vendor call stays inside the MSU lab machine's travel (X ~283 mm, Y ~85 mm):
from start (20, 20), commanding end (40, 35) lands at

    absolute            -> (40, 35)
    absolute, swapped   -> (35, 40)
    relative            -> (60, 55)
    relative, swapped   -> (55, 60)

all of which are safe on both axes, and all four are distinguishable.

SAFETY
  * Home switches must be wired and verified (they are — bench B1 2026-08-11).
  * Keep a hand on the physical e-stop for the FIRST Line_2Axis dispatch.
  * The optional --with-limit phase deliberately drives Y into its positive
    switch at creep speed (single-axis bench B1 showed the controller
    hardware-stops with 0.000 mm overrun; this verifies the same for a line).

Usage (Windows, lab PC):
  python libs/fmc-motion/tools/hw_line_bench.py --ip 192.168.0.30 --port 8088

Results are printed and saved to line_bench_results.json for the PRD log.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import math
import sys
import time
from ctypes import byref, c_float

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "src"))

from fmc_motion.fmc006ge31 import _MachineStatus, _bind, vendor_dll_path  # noqa: E402

RESULTS: dict[str, object] = {}

START = (20.0, 20.0)     # absolute start point (axis_a, axis_b), mm
END = (40.0, 35.0)       # commanded line end (endX=lower axis assumed), mm
TOLERANCE_MM = 0.5


def ask(prompt: str) -> bool:
    return input(f"{prompt} [y/N] ").strip().lower() == "y"


def get_status(dll, dev_id, buf) -> _MachineStatus:
    rc = dll.FMC006GE31_Get_Machine_Status(dev_id, byref(buf))
    if rc != 0:
        raise RuntimeError(f"Get_Machine_Status rc={rc}")
    return buf


def positions(dll, dev_id, buf, axes) -> tuple[float, float]:
    ms = get_status(dll, dev_id, buf)
    return float(ms.realPos[axes[0]]), float(ms.realPos[axes[1]])


def both_stopped(dll, dev_id, axes) -> bool:
    return all(dll.FMC006GE31_Check_Axis_Is_Stop(dev_id, a) == 1 for a in axes)


def wait_both_stopped(dll, dev_id, axes, timeout=120.0) -> bool:
    """1 s start grace + 2 consecutive stopped reads (lab findings 2026-08-11)."""
    time.sleep(1.0)
    end = time.monotonic() + timeout
    settled = 0
    while time.monotonic() < end:
        settled = settled + 1 if both_stopped(dll, dev_id, axes) else 0
        if settled >= 2:
            return True
        time.sleep(0.1)
    return False


def move_abs(dll, dev_id, axis, target, speed=20.0) -> None:
    rc = dll.FMC006GE31_Jog_Single_Axis(dev_id, axis, c_float(target),
                                        c_float(speed), c_float(200), c_float(200), 2)
    if rc != 0:
        raise RuntimeError(f"Jog_Single_Axis(axis={axis}) rc={rc}")
    if not wait_both_stopped(dll, dev_id, [axis]):
        raise RuntimeError(f"axis {axis} did not stop")


def dispatch_line(dll, dev_id, axes, end_a, end_b, speed, accdec=150.0) -> int:
    mask = (1 << axes[0]) | (1 << axes[1])
    return dll.FMC006GE31_Line_2Axis(dev_id, mask, c_float(end_a), c_float(end_b),
                                     c_float(speed), c_float(accdec), c_float(accdec))


def home_and_position(dll, dev_id, buf, axes) -> bool:
    print("\n=== setup: home both axes, move to start ===")
    if not ask(f"Home axes {axes} to their negative switches, then move to "
               f"({START[0]}, {START[1]}) mm?"):
        return False
    for a in axes:
        print(f"  homing axis {a}...")
        rc = dll.FMC006GE31_Home_Single_Axis(dev_id, a, c_float(15.0),
                                             c_float(150.0), c_float(2.0), 2)
        if rc != 0 or not wait_both_stopped(dll, dev_id, [a], timeout=120):
            print(f"  homing axis {a} failed (rc={rc})")
            return False
    move_abs(dll, dev_id, axes[0], START[0])
    move_abs(dll, dev_id, axes[1], START[1])
    pos = positions(dll, dev_id, buf, axes)
    print(f"  at start: ({pos[0]:.3f}, {pos[1]:.3f})")
    return True


def bench_bl1_bl2(dll, dev_id, buf, axes, speed) -> None:
    print("\n=== B-L1/B-L2: endpoint semantics + composite speed ===")
    print(f"  Line_2Axis from {START} commanding end {END} at {speed} mm/s")
    if not ask("Hand on e-stop (first interpolated dispatch ever). Proceed?"):
        return
    t0 = time.monotonic()
    rc = dispatch_line(dll, dev_id, axes, END[0], END[1], speed)
    print(f"  dispatch rc={rc}")
    if rc != 0:
        RESULTS["BL1_endpoint_semantics"] = {"dispatch_rc": rc,
                                             "verdict": "dispatch-failed"}
        return

    samples: list[tuple[float, float, float, float, float]] = []
    time.sleep(1.0)  # start grace
    settled = 0
    while settled < 2 and time.monotonic() - t0 < 120:
        ms = get_status(dll, dev_id, buf)
        samples.append((time.monotonic() - t0,
                        float(ms.realPos[axes[0]]), float(ms.realPos[axes[1]]),
                        abs(float(ms.realSpeed[axes[0]])),
                        abs(float(ms.realSpeed[axes[1]]))))
        settled = settled + 1 if both_stopped(dll, dev_id, axes) else 0
        time.sleep(0.05)
    duration = time.monotonic() - t0
    final = positions(dll, dev_id, buf, axes)
    print(f"  final: ({final[0]:.3f}, {final[1]:.3f}) after {duration:.2f} s")

    hypotheses = {
        "absolute": (END[0], END[1]),
        "absolute_swapped": (END[1], END[0]),
        "relative": (START[0] + END[0], START[1] + END[1]),
        "relative_swapped": (START[0] + END[1], START[1] + END[0]),
    }
    verdict = "neither"
    for name, (ea, eb) in hypotheses.items():
        if abs(final[0] - ea) < TOLERANCE_MM and abs(final[1] - eb) < TOLERANCE_MM:
            verdict = name
            break

    # straightness + sync against the line actually traced (start -> final)
    dx, dy = final[0] - START[0], final[1] - START[1]
    length = math.hypot(dx, dy)
    max_dev = 0.0
    if length > 1e-6:
        for _, pa, pb, _, _ in samples:
            dev = abs((pa - START[0]) * dy - (pb - START[1]) * dx) / length
            max_dev = max(max_dev, dev)
    peak_a = max((s[3] for s in samples), default=0.0)
    peak_b = max((s[4] for s in samples), default=0.0)
    moved_together = any(s[3] > 0.5 and s[4] > 0.5 for s in samples)
    expected_peaks = (speed * abs(dx) / length if length else 0.0,
                      speed * abs(dy) / length if length else 0.0)

    RESULTS["BL1_endpoint_semantics"] = {
        "dispatch_rc": rc,
        "commanded_end": END,
        "start": START,
        "final": [round(final[0], 3), round(final[1], 3)],
        "verdict": verdict,
        "library_assumption_ok": verdict == "absolute",
    }
    RESULTS["BL2_composite_speed"] = {
        "commanded_composite_mm_s": speed,
        "path_length_mm": round(length, 3),
        "duration_s_incl_latency": round(duration, 2),
        "peak_axis_speeds_mm_s": [round(peak_a, 2), round(peak_b, 2)],
        "expected_if_composite": [round(expected_peaks[0], 2),
                                  round(expected_peaks[1], 2)],
        "axes_moved_simultaneously": moved_together,
        "max_path_deviation_mm": round(max_dev, 3),
        "samples": len(samples),
    }
    print(f"  verdict: {verdict}"
          + ("  (matches the library's assumption)" if verdict == "absolute"
             else "  (LIBRARY ASSUMPTION WRONG - fix _ll_line before printing!)"))
    print(f"  peak speeds {peak_a:.1f}/{peak_b:.1f} mm/s "
          f"(composite prediction {expected_peaks[0]:.1f}/{expected_peaks[1]:.1f}); "
          f"max path deviation {max_dev:.3f} mm")


def bench_busy(dll, dev_id, buf, axes, speed) -> None:
    print("\n=== B-L2b: Line_2Axis to busy axes ===")
    pos = positions(dll, dev_id, buf, axes)
    # run toward whichever endpoint is farther so the line is never zero-length
    target = END if math.hypot(pos[0] - START[0], pos[1] - START[1]) < 1.0 else START
    if not ask(f"Run a line to {target} and fire a second line mid-move?"):
        return
    rc1 = dispatch_line(dll, dev_id, axes, target[0], target[1], speed)
    time.sleep(0.8)
    rc2 = dispatch_line(dll, dev_id, axes, target[0], target[1], speed)
    completed = wait_both_stopped(dll, dev_id, axes)
    final = positions(dll, dev_id, buf, axes)
    RESULTS["BL2b_busy_line"] = {"first_rc": rc1, "second_rc_while_moving": rc2,
                                 "completed": completed,
                                 "final": [round(final[0], 3), round(final[1], 3)]}
    print(f"  first rc={rc1}, second (busy) rc={rc2} "
          f"(-10 expected); completed={completed}")


def bench_bl3_pause(dll, dev_id, buf, axes, speed) -> None:
    print("\n=== B-L3: Pause_Run / Resume_Run on an interpolated move ===")
    if not ask(f"Reposition to {START}, then line to {END} pausing mid-move "
               f"(mask = both axes)?"):
        return
    move_abs(dll, dev_id, axes[0], START[0])
    move_abs(dll, dev_id, axes[1], START[1])
    mask = (1 << axes[0]) | (1 << axes[1])
    rc = dispatch_line(dll, dev_id, axes, END[0], END[1], speed)
    if rc != 0:
        RESULTS["BL3_pause_on_line"] = {"dispatch_rc": rc}
        return
    time.sleep(1.2)
    rc_pause = dll.FMC006GE31_Pause_Run(dev_id, mask)
    time.sleep(1.0)
    p1 = positions(dll, dev_id, buf, axes)
    time.sleep(1.0)
    p2 = positions(dll, dev_id, buf, axes)
    held = abs(p2[0] - p1[0]) < 0.01 and abs(p2[1] - p1[1]) < 0.01
    rc_resume = dll.FMC006GE31_Resume_Run(dev_id, mask)
    completed = wait_both_stopped(dll, dev_id, axes)
    final = positions(dll, dev_id, buf, axes)
    on_target = (abs(final[0] - END[0]) < TOLERANCE_MM
                 and abs(final[1] - END[1]) < TOLERANCE_MM)
    RESULTS["BL3_pause_on_line"] = {
        "pause_rc": rc_pause, "resume_rc": rc_resume,
        "held_position_while_paused": held,
        "completed_after_resume": completed,
        "reached_original_target": on_target,
        "final": [round(final[0], 3), round(final[1], 3)],
    }
    print(f"  pause rc={rc_pause} held={held} resume rc={rc_resume} "
          f"completed={completed} on_target={on_target}")
    print("  -> if all good, FR-LINE-04 can be relaxed to allow pause on lines")


def bench_bl4_limit(dll, dev_id, buf, axes, creep=5.0) -> None:
    print("\n=== B-L4 (optional): limit trip during a line move ===")
    print(f"  Drives a shallow line: axis {axes[0]} +30 mm while axis {axes[1]}")
    print(f"  creeps INTO its positive switch at ~{creep} mm/s composite.")
    if not ask("This intentionally hits Y's positive switch. Proceed?"):
        return
    start = positions(dll, dev_id, buf, axes)
    # target B far past its ~85 mm travel; A stays well inside its range
    rc = dispatch_line(dll, dev_id, axes, start[0] + 30.0, 120.0, creep)
    if rc != 0:
        RESULTS["BL4_limit_trip_line"] = {"dispatch_rc": rc}
        return
    trip_pos = None
    behavior = "not-reached"
    t_end = time.monotonic() + 180
    time.sleep(1.0)
    while time.monotonic() < t_end:
        ms = get_status(dll, dev_id, buf)
        if ms.limitPStatus & (1 << axes[1]):
            trip_pos = positions(dll, dev_id, buf, axes)
            print(f"  limit tripped at {trip_pos}; observing controller...")
            time.sleep(0.5)
            ms = get_status(dll, dev_id, buf)
            moving = any(abs(float(ms.realSpeed[a])) > 0.01 for a in axes)
            if moving:
                behavior = "kept-moving (library monitoring REQUIRED)"
                for a in axes:
                    dll.FMC006GE31_Stop_Single_Axis(dev_id, a, 2)
            else:
                stopped_both = both_stopped(dll, dev_id, axes)
                behavior = f"hardware-stopped both={stopped_both}"
            break
        if both_stopped(dll, dev_id, axes):
            behavior = "stopped-before-trip (unexpected - check wiring)"
            break
        time.sleep(0.1)
    else:
        for a in axes:
            dll.FMC006GE31_Stop_Single_Axis(dev_id, a, 2)
        behavior = "timeout"
    final = positions(dll, dev_id, buf, axes)
    RESULTS["BL4_limit_trip_line"] = {
        "behavior": behavior,
        "trip_positions": list(trip_pos) if trip_pos else None,
        "final": [round(final[0], 3), round(final[1], 3)],
    }
    print(f"  result: {RESULTS['BL4_limit_trip_line']}")
    if ask("Back away from the switch (Y back 20 mm)?"):
        move_abs(dll, dev_id, axes[1], max(final[1] - 20.0, 5.0))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ip", default="192.168.0.30")
    ap.add_argument("--port", type=int, default=8088)
    ap.add_argument("--id", type=int, default=1, dest="dev_id")
    ap.add_argument("--axis-a", type=int, default=0, help="first (long) axis")
    ap.add_argument("--axis-b", type=int, default=1, help="second (short) axis")
    ap.add_argument("--speed", type=float, default=15.0,
                    help="composite line speed, mm/s")
    ap.add_argument("--with-limit", action="store_true",
                    help="also run B-L4 (drives Y into its positive switch)")
    ap.add_argument("--out", default="line_bench_results.json")
    args = ap.parse_args()

    if sys.platform != "win32":
        raise SystemExit("bench tests require Windows (vendor DLL)")

    axes = (args.axis_a, args.axis_b)
    dll = _bind(ctypes.WinDLL(str(vendor_dll_path())))
    rc = -1
    for attempt in range(3):  # transient rc=-5 observed on first connect
        rc = dll.FMC006GE31_Open_Device(args.dev_id, args.ip.encode(), args.port, 0)
        if rc == 0:
            break
        print(f"Open_Device attempt {attempt + 1} failed rc={rc}; retrying...")
        dll.FMC006GE31_Close_Device(args.dev_id)
        time.sleep(1.5)
    if rc != 0:
        raise SystemExit(f"Open_Device failed rc={rc} (if a previous run "
                         f"crashed without Close_Device, power-cycle the controller)")
    print(f"connected to {args.ip}:{args.port} (id={args.dev_id}) axes={axes}")
    buf = _MachineStatus()
    try:
        if home_and_position(dll, args.dev_id, buf, axes):
            bench_bl1_bl2(dll, args.dev_id, buf, axes, args.speed)
            bench_busy(dll, args.dev_id, buf, axes, args.speed)
            bench_bl3_pause(dll, args.dev_id, buf, axes, args.speed)
            if args.with_limit:
                bench_bl4_limit(dll, args.dev_id, buf, axes)
    finally:
        dll.FMC006GE31_Close_Device(args.dev_id)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(RESULTS, f, indent=2)
    print(f"\nresults written to {args.out}")
    print(json.dumps(RESULTS, indent=2))


if __name__ == "__main__":
    main()

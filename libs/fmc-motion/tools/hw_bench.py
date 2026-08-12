"""Hardware bench tests B1-B4 (PRD section 10) — run on the lab machine.

These answer the vendor-manual gaps directly against the raw DLL, one axis at a
time. Each test asks for confirmation before moving anything.

SAFETY
  * Use ONE axis with clear travel. Keep a hand on the physical e-stop.
  * B1 intentionally drives slowly INTO a limit switch. Confirm the switch is
    wired and the mechanics tolerate a slow touch (creep speed, default 5 mm/s).

Usage (Windows, lab PC):
  python libs/fmc-motion/tools/hw_bench.py --ip 192.168.0.30 --port 8088 --axis 0

Results are printed and saved to hw_bench_results.json for the PRD log.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import statistics
import sys
import time
from ctypes import byref, c_float

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "src"))

from fmc_motion.fmc006ge31 import _MachineStatus, _bind, vendor_dll_path  # noqa: E402

RESULTS: dict[str, object] = {}


def ask(prompt: str) -> bool:
    return input(f"{prompt} [y/N] ").strip().lower() == "y"


def get_status(dll, dev_id, buf) -> _MachineStatus:
    rc = dll.FMC006GE31_Get_Machine_Status(dev_id, byref(buf))
    if rc != 0:
        raise RuntimeError(f"Get_Machine_Status rc={rc}")
    return buf


def wait_axis_stop(dll, dev_id, axis, timeout=60.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if dll.FMC006GE31_Check_Axis_Is_Stop(dev_id, axis) == 1:
            return True
        time.sleep(0.05)
    return False


def bench_b3_latency(dll, dev_id, buf, n=200) -> None:
    print(f"\n=== B3: status round-trip latency ({n} calls) ===")
    samples = []
    for _ in range(n):
        t0 = time.perf_counter()
        get_status(dll, dev_id, buf)
        samples.append((time.perf_counter() - t0) * 1000.0)
    result = {
        "avg_ms": round(statistics.mean(samples), 3),
        "p95_ms": round(sorted(samples)[int(n * 0.95)], 3),
        "max_ms": round(max(samples), 3),
    }
    RESULTS["B3_status_latency"] = result
    print(f"  {result}")
    print(f"  -> safe status poll rate <= {1000 / (2 * result['p95_ms']):.0f} Hz; "
          f"suggested poll_interval_s >= {2 * result['p95_ms'] / 1000:.3f}")


def bench_b2_busy(dll, dev_id, axis, dist, speed) -> None:
    print("\n=== B2: command to a busy axis ===")
    if not ask(f"Move axis {axis} by {dist} mm at {speed} mm/s (twice, overlapping)?"):
        return
    rc1 = dll.FMC006GE31_Jog_Single_Axis(dev_id, axis, c_float(dist),
                                         c_float(speed), c_float(200), c_float(200), 1)
    time.sleep(0.5)  # let it actually start
    rc2 = dll.FMC006GE31_Jog_Single_Axis(dev_id, axis, c_float(dist),
                                         c_float(speed), c_float(200), c_float(200), 1)
    stopped = wait_axis_stop(dll, dev_id, axis)
    RESULTS["B2_busy_axis"] = {"first_rc": rc1, "second_rc_while_moving": rc2,
                               "completed": stopped}
    print(f"  first rc={rc1}, second (busy) rc={rc2}  "
          f"(-10 expected per manual; 0 would mean silently ignored)")
    if ask("Move back to start position?"):
        dll.FMC006GE31_Jog_Single_Axis(dev_id, axis, c_float(-dist), c_float(speed),
                                       c_float(200), c_float(200), 1)
        wait_axis_stop(dll, dev_id, axis)


def bench_b4_pause_resume(dll, dev_id, axis, dist, speed) -> None:
    print("\n=== B4: Pause_Run / Resume_Run (undocumented) ===")
    if not ask(f"Move axis {axis} by {dist} mm and pause mid-way?"):
        return
    axis_mask = 1 << axis
    dll.FMC006GE31_Jog_Single_Axis(dev_id, axis, c_float(dist), c_float(speed),
                                   c_float(200), c_float(200), 1)
    time.sleep(1.0)
    rc_pause = dll.FMC006GE31_Pause_Run(dev_id, axis_mask)
    time.sleep(1.0)
    pos = c_float()
    dll.FMC006GE31_Get_Axis_Current_Pos(dev_id, axis, byref(pos))
    paused_pos1 = pos.value
    time.sleep(1.0)
    dll.FMC006GE31_Get_Axis_Current_Pos(dev_id, axis, byref(pos))
    paused_pos2 = pos.value
    actually_paused = abs(paused_pos2 - paused_pos1) < 0.01
    rc_resume = dll.FMC006GE31_Resume_Run(dev_id, axis_mask)
    completed = wait_axis_stop(dll, dev_id, axis)
    RESULTS["B4_pause_resume"] = {
        "pause_rc": rc_pause, "resume_rc": rc_resume,
        "held_position_while_paused": actually_paused,
        "completed_after_resume": completed,
    }
    print(f"  pause rc={rc_pause} held={actually_paused} "
          f"resume rc={rc_resume} completed={completed}")
    if ask("Move back to start position?"):
        dll.FMC006GE31_Jog_Single_Axis(dev_id, axis, c_float(-dist), c_float(speed),
                                       c_float(200), c_float(200), 1)
        wait_axis_stop(dll, dev_id, axis)


def bench_b1_limit_hit(dll, dev_id, buf, axis, creep_speed, max_search) -> None:
    print("\n=== B1: behavior when a limit switch trips mid-move ===")
    print(f"  First HOMES axis {axis} to the negative switch (known frame),")
    print(f"  then drives at {creep_speed} mm/s toward the POSITIVE limit")
    print(f"  switch (up to {max_search} mm) and watches what the controller")
    print("  does when the switch trips.")
    if not ask("Travel clear, switches wired, hand on e-stop. Proceed?"):
        return
    print("  homing to negative limit...")
    rc = dll.FMC006GE31_Home_Single_Axis(dev_id, axis, c_float(15.0),
                                         c_float(150.0), c_float(2.0), 2)
    if rc != 0 or not wait_axis_stop(dll, dev_id, axis, timeout=120):
        RESULTS["B1_limit_hit"] = {"behavior": f"homing failed (rc={rc})"}
        print(f"  homing failed rc={rc}; skipping B1")
        return
    print("  homed; creeping toward positive switch...")
    dll.FMC006GE31_Jog_Single_Axis(dev_id, axis, c_float(max_search),
                                   c_float(creep_speed), c_float(100), c_float(100), 1)
    trip_pos = None
    behavior = "not-reached"
    t_end = time.monotonic() + (max_search / creep_speed) + 10
    while time.monotonic() < t_end:
        ms = get_status(dll, dev_id, buf)
        if ms.limitPStatus & (1 << axis):
            trip_pos = float(ms.realPos[axis])
            print(f"  limit tripped at {trip_pos:.3f} mm; observing controller...")
            # does the controller stop on its own?
            time.sleep(0.5)
            ms = get_status(dll, dev_id, buf)
            moving = abs(float(ms.realSpeed[axis])) > 0.01
            stopped_flag = dll.FMC006GE31_Check_Axis_Is_Stop(dev_id, axis) == 1
            if moving:
                behavior = "kept-moving (library MUST stop it -> keep FR-HOME-07)"
                dll.FMC006GE31_Stop_Single_Axis(dev_id, axis, 2)
            else:
                behavior = ("hardware-stopped"
                            + (" (is_stop=1)" if stopped_flag else " (is_stop=0!)"))
            break
        if dll.FMC006GE31_Check_Axis_Is_Stop(dev_id, axis) == 1:
            behavior = "finished-search-distance-without-trip (check wiring)"
            break
        time.sleep(0.05)
    else:
        dll.FMC006GE31_Stop_Single_Axis(dev_id, axis, 2)
        behavior = "timeout"
    ms = get_status(dll, dev_id, buf)
    RESULTS["B1_limit_hit"] = {
        "behavior": behavior,
        "trip_position_mm": trip_pos,
        "final_position_mm": float(ms.realPos[axis]),
        "overrun_mm": (round(float(ms.realPos[axis]) - trip_pos, 3)
                       if trip_pos is not None else None),
    }
    print(f"  result: {RESULTS['B1_limit_hit']}")
    if ask("Move away from the limit (back 20 mm)?"):
        dll.FMC006GE31_Jog_Single_Axis(dev_id, axis, c_float(-20.0), c_float(20.0),
                                       c_float(200), c_float(200), 1)
        wait_axis_stop(dll, dev_id, axis)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ip", default="192.168.0.30")
    ap.add_argument("--port", type=int, default=8088)
    ap.add_argument("--id", type=int, default=1, dest="dev_id")
    ap.add_argument("--axis", type=int, default=0)
    ap.add_argument("--dist", type=float, default=20.0, help="test move distance, mm")
    ap.add_argument("--speed", type=float, default=20.0, help="test move speed, mm/s")
    ap.add_argument("--creep", type=float, default=5.0, help="B1 creep speed, mm/s")
    ap.add_argument("--max-search", type=float, default=300.0,
                    help="B1 max distance toward the limit, mm")
    ap.add_argument("--out", default="hw_bench_results.json")
    args = ap.parse_args()

    if sys.platform != "win32":
        raise SystemExit("bench tests require Windows (vendor DLL)")

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
    print(f"connected to {args.ip}:{args.port} (id={args.dev_id})")
    buf = _MachineStatus()
    try:
        bench_b3_latency(dll, args.dev_id, buf)
        bench_b2_busy(dll, args.dev_id, args.axis, args.dist, args.speed)
        bench_b4_pause_resume(dll, args.dev_id, args.axis, args.dist, args.speed)
        bench_b1_limit_hit(dll, args.dev_id, buf, args.axis, args.creep,
                           args.max_search)
    finally:
        dll.FMC006GE31_Close_Device(args.dev_id)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(RESULTS, f, indent=2)
    print(f"\nresults written to {args.out}")
    print(json.dumps(RESULTS, indent=2))


if __name__ == "__main__":
    main()

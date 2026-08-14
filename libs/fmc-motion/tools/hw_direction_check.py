"""Motor-direction polarity probe — run after any vendor parameter edit.

Context (lab 2026-08-13): after the X/Y lead correction in the vendor tool,
homing commanded toward the NEGATIVE switch physically drove both axes to the
POSITIVE end — a direction/polarity parameter flipped alongside the edit.
This probe answers "does commanded-negative move physically negative?" per
axis with one small creep move, without homing and without trusting the
(possibly garbage) coordinate zero.

Method per axis, raw DLL:
  1. read position + limit bits;
  2. command a RELATIVE −5 mm at 5 mm/s (commanded negative, creep speed —
     if polarity is inverted and the axis sits on its + switch this nudges
     into the switch; bench B1 showed the controller hardware-stops with
     ~0 mm overrun at creep speed, so keep a hand on e-stop anyway);
  3. classify: if the + switch was active and RELEASES -> polarity NORMAL;
     if it stays active (or the axis refuses to move) -> INVERTED. If no
     switch was active, the operator confirms the physical direction by eye.

Usage (Windows, lab PC):
  python libs/fmc-motion/tools/hw_direction_check.py --axes 0,1,2

Results are printed and saved to direction_check_results.json.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import sys
import time
from ctypes import byref, c_float

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "src"))

from fmc_motion.fmc006ge31 import _MachineStatus, _bind, vendor_dll_path  # noqa: E402

PROBE_MM = 5.0
PROBE_SPEED = 5.0
RESULTS: dict[str, object] = {}


def ask(prompt: str) -> bool:
    return input(f"{prompt} [y/N] ").strip().lower() == "y"


def get_status(dll, dev_id, buf) -> _MachineStatus:
    rc = dll.FMC006GE31_Get_Machine_Status(dev_id, byref(buf))
    if rc != 0:
        raise RuntimeError(f"Get_Machine_Status rc={rc}")
    return buf


def snapshot(dll, dev_id, buf, axis) -> dict:
    ms = get_status(dll, dev_id, buf)
    return {
        "pos": round(float(ms.realPos[axis]), 3),
        "limit_negative": bool(ms.limitNStatus & (1 << axis)),
        "limit_positive": bool(ms.limitPStatus & (1 << axis)),
    }


def wait_stop(dll, dev_id, axis, timeout=30.0) -> bool:
    time.sleep(1.0)  # start grace (lab 2026-08-11)
    end = time.monotonic() + timeout
    settled = 0
    while time.monotonic() < end:
        settled = settled + 1 if dll.FMC006GE31_Check_Axis_Is_Stop(dev_id, axis) == 1 else 0
        if settled >= 2:
            return True
        time.sleep(0.1)
    return False


def probe_axis(dll, dev_id, buf, axis) -> None:
    print(f"\n=== axis {axis}: polarity probe ===")
    before = snapshot(dll, dev_id, buf, axis)
    print(f"  before: pos={before['pos']} mm  "
          f"N-switch={'ON' if before['limit_negative'] else 'off'}  "
          f"P-switch={'ON' if before['limit_positive'] else 'off'}")
    if before["limit_negative"] and before["limit_positive"]:
        RESULTS[f"axis{axis}"] = {"verdict": "both-switches-active (wiring?)"}
        return
    if not ask(f"Command axis {axis} RELATIVE -{PROBE_MM} mm at {PROBE_SPEED} mm/s "
               f"(hand on e-stop)?"):
        RESULTS[f"axis{axis}"] = {"verdict": "skipped"}
        return
    rc = dll.FMC006GE31_Jog_Single_Axis(dev_id, axis, c_float(-PROBE_MM),
                                        c_float(PROBE_SPEED), c_float(100),
                                        c_float(100), 1)
    stopped = wait_stop(dll, dev_id, axis)
    after = snapshot(dll, dev_id, buf, axis)
    moved = round(after["pos"] - before["pos"], 3)
    print(f"  after:  pos={after['pos']} mm (reported moved {moved:+.3f})  "
          f"N-switch={'ON' if after['limit_negative'] else 'off'}  "
          f"P-switch={'ON' if after['limit_positive'] else 'off'}  rc={rc}")

    if before["limit_positive"]:
        if not after["limit_positive"]:
            verdict = "NORMAL (commanded-negative released the + switch)"
        else:
            verdict = ("INVERTED (still on the + switch after a commanded "
                       "negative move) - fix the direction parameter in the "
                       "vendor tool")
    elif before["limit_negative"] and after["limit_negative"] and abs(moved) < 0.5:
        verdict = ("on the - switch and refused to move negative: expected; "
                   "re-run after backing off to classify")
    else:
        eyes = ask("  Did the axis physically move toward the HOME "
                   "(negative-switch) end?")
        verdict = ("NORMAL (operator confirmed physical negative motion)"
                   if eyes else
                   "INVERTED (operator observed positive motion on a "
                   "commanded-negative move) - fix the vendor direction "
                   "parameter")
    print(f"  verdict: {verdict}")
    RESULTS[f"axis{axis}"] = {"before": before, "after": after,
                              "reported_moved_mm": moved, "dispatch_rc": rc,
                              "stopped": stopped, "verdict": verdict}
    if "INVERTED" in verdict and after["limit_positive"]:
        if ask(f"  Back axis {axis} off the + switch (commanded +20 mm, which "
               f"is physically negative under inversion)?"):
            dll.FMC006GE31_Jog_Single_Axis(dev_id, axis, c_float(20.0),
                                           c_float(10.0), c_float(100),
                                           c_float(100), 1)
            wait_stop(dll, dev_id, axis, timeout=10.0)
            print(f"  now: {snapshot(dll, dev_id, buf, axis)}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ip", default="192.168.0.30")
    ap.add_argument("--port", type=int, default=8088)
    ap.add_argument("--id", type=int, default=1, dest="dev_id")
    ap.add_argument("--axes", default="0,1", help="comma-separated axis indices")
    ap.add_argument("--out", default="direction_check_results.json")
    args = ap.parse_args()

    if sys.platform != "win32":
        raise SystemExit("direction check requires Windows (vendor DLL)")

    dll = _bind(ctypes.WinDLL(str(vendor_dll_path())))
    rc = -1
    for attempt in range(3):
        rc = dll.FMC006GE31_Open_Device(args.dev_id, args.ip.encode(), args.port, 0)
        if rc == 0:
            break
        dll.FMC006GE31_Close_Device(args.dev_id)
        time.sleep(1.5)
    if rc != 0:
        raise SystemExit(f"Open_Device failed rc={rc}")
    print(f"connected to {args.ip}:{args.port}")
    buf = _MachineStatus()
    try:
        for axis in [int(a) for a in args.axes.split(",")]:
            probe_axis(dll, args.dev_id, buf, axis)
    finally:
        dll.FMC006GE31_Close_Device(args.dev_id)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(RESULTS, f, indent=2)
    print(f"\nresults written to {args.out}")


if __name__ == "__main__":
    main()

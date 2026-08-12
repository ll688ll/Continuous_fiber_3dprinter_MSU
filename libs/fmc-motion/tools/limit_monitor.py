"""Live limit-switch / position monitor — verify switch wiring and bit mapping.

Motivation (bench 2026-08-11): axis 1 was commanded +300 mm but the controller
stopped it at ~82 mm on its own, and bit 1 of limitPStatus never read as set.
Either that switch is wired to a different input, the bit mapping differs from
the assumed one-bit-per-axis layout, or the switch is faulty.

Nothing moves. Press each limit switch BY HAND and watch which bit changes.

Usage (lab machine):
  python libs/fmc-motion/tools/limit_monitor.py --ip 192.168.0.30

Output per line: raw hex of limitNStatus/limitPStatus + per-axis decoded bits
+ positions. Ctrl+C to exit.
"""

from __future__ import annotations

import argparse
import ctypes
import sys
import time
from ctypes import byref

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "src"))

from fmc_motion.fmc006ge31 import _MachineStatus, _bind, vendor_dll_path  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ip", default="192.168.0.30")
    ap.add_argument("--port", type=int, default=8088)
    ap.add_argument("--id", type=int, default=1, dest="dev_id")
    args = ap.parse_args()

    if sys.platform != "win32":
        raise SystemExit("requires Windows (vendor DLL)")

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

    buf = _MachineStatus()
    print("press each limit switch by hand; Ctrl+C to exit")
    print("axis:            0     1     2     3     4     5")
    last_line = ""
    try:
        while True:
            rc = dll.FMC006GE31_Get_Machine_Status(args.dev_id, byref(buf))
            if rc != 0:
                print(f"Get_Machine_Status rc={rc}")
                time.sleep(1.0)
                continue
            n, p = buf.limitNStatus, buf.limitPStatus
            bits_n = " ".join("N" if n & (1 << i) else "." for i in range(6))
            bits_p = " ".join("P" if p & (1 << i) else "." for i in range(6))
            positions = " ".join(f"{buf.realPos[i]:8.2f}" for i in range(6))
            line = (f"limN=0x{n:08X} [{bits_n}]  limP=0x{p:08X} [{bits_p}]  "
                    f"in=0x{buf.inputStatus:08X}  estop={buf.emergencyStatus}")
            if line != last_line:  # only print on change to keep the log readable
                print(f"{time.strftime('%H:%M:%S')}  {line}")
                print(f"          pos: {positions}")
                last_line = line
            time.sleep(0.25)
    except KeyboardInterrupt:
        pass
    finally:
        dll.FMC006GE31_Close_Device(args.dev_id)
        print("disconnected")


if __name__ == "__main__":
    main()

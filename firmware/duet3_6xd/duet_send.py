#!/usr/bin/env python3
"""Send G-code to a Duet 3 over USB serial and print the replies.

Duet 3 boards enumerate as a USB CDC device (VID 0x1D50, PID 0x60EC/0x60EE),
so the baud rate is ignored -- any value works. RepRapFirmware answers each
command with zero or more reply lines followed by "ok".

    pip install pyserial

    python duet_send.py M115                     # auto-detect the port
    python duet_send.py --port COM3 M115 M122
    python duet_send.py --file bringup.g
    python duet_send.py --list                   # show candidate ports
"""
from __future__ import annotations

import argparse
import sys
import time

try:
    import serial
    from serial.tools import list_ports
except ImportError:  # pragma: no cover - environment problem, not logic
    sys.exit("pyserial is not installed.  Run:  pip install pyserial")

DUET_IDS = {(0x1D50, 0x60EC), (0x1D50, 0x60EE)}
REPLY_TIMEOUT_S = 10.0


def candidates() -> list:
    return [p for p in list_ports.comports() if (p.vid, p.pid) in DUET_IDS]


def pick_port(explicit: str | None) -> str:
    if explicit:
        return explicit
    found = candidates()
    if not found:
        seen = ", ".join(f"{p.device} ({p.description})" for p in list_ports.comports())
        sys.exit(f"No Duet found. Ports present: {seen or 'none'}\nUse --port to force one.")
    if len(found) > 1:
        names = ", ".join(p.device for p in found)
        sys.exit(f"More than one Duet found ({names}). Pick one with --port.")
    return found[0].device


def send(ser: serial.Serial, line: str) -> bool:
    """Write one command, echo every reply line. Returns False on an Error: reply."""
    line = line.strip()
    if not line or line.startswith(";"):
        return True
    print(f">>> {line}")
    ser.reset_input_buffer()
    ser.write((line + "\n").encode("ascii", "replace"))
    ser.flush()

    ok = True
    deadline = time.monotonic() + REPLY_TIMEOUT_S
    while time.monotonic() < deadline:
        raw = ser.readline()
        if not raw:
            continue
        reply = raw.decode("utf-8", "replace").rstrip()
        if not reply:
            continue
        if reply.lower().startswith("ok"):
            trailing = reply[2:].strip()
            if trailing:
                print(f"    {trailing}")
            return ok
        if reply.startswith("Error"):
            ok = False
        print(f"    {reply}")
    print(f"    !! no 'ok' within {REPLY_TIMEOUT_S:.0f}s")
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("gcode", nargs="*", help="commands to send, in order")
    ap.add_argument("--port", help="COM port (default: auto-detect a Duet)")
    ap.add_argument("--baud", type=int, default=115200, help="ignored by CDC, kept for tools")
    ap.add_argument("--file", help="send a G-code file line by line")
    ap.add_argument("--list", action="store_true", help="list serial ports and exit")
    ap.add_argument("--stop-on-error", action="store_true",
                    help="abort the sequence on the first Error: reply")
    args = ap.parse_args()

    if args.list:
        for p in list_ports.comports():
            tag = "  <-- Duet" if (p.vid, p.pid) in DUET_IDS else ""
            print(f"{p.device:8} {p.description}{tag}")
        return 0

    lines = list(args.gcode)
    if args.file:
        with open(args.file, "r", encoding="utf-8") as fh:
            lines.extend(fh.read().splitlines())
    if not lines:
        ap.error("nothing to send: give commands, --file, or --list")

    port = pick_port(args.port)
    print(f"# {port}")
    with serial.Serial(port, args.baud, timeout=0.5) as ser:
        time.sleep(0.2)          # let the CDC endpoint settle
        ser.reset_input_buffer()
        for line in lines:
            if not send(ser, line) and args.stop_on_error:
                print("aborted on error", file=sys.stderr)
                return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

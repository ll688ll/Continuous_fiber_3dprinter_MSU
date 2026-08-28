#!/usr/bin/env python3
"""Stream G-code to a Duet 3 over HTTP, line by line, with flow control.

This is "mode A": the master machine holds the program and feeds the board.
Mode B -- upload with rr_upload and run with M32 -- is simpler and is what
duet_send.py / the SD card do. Use mode A when the host needs to stay in the
loop: injecting tool commands, reacting to sensors, or generating the path
on the fly.

Flow control is the whole problem. rr_gcode returns {"buff": N}, the free space
in the board's G-code input buffer (250 bytes). Send more than that and lines
are silently lost -- there is no error, you just get a part with a missing
segment. So batches are built by BYTES against that buffer, never by line
count, and --batch is only an upper bound on lines.

Measured 2026-08-25 on a 100 Mbit link, with 4-byte commands:

    1 line/req     15.4 lines/s
    5 lines/req    79.2 lines/s
   10 lines/req   163.0 lines/s
   20 lines/req   343.3 lines/s

Read that table carefully: it used 4-byte commands, so 20 lines fit in 99
bytes. Real G-code lines are ~23 bytes, so only ~10 fit in the 250-byte
buffer and ~163 lines/s is the honest ceiling. The machine needs roughly
6 moves/s on average for the reference Cura file and bursts to ~38 moves/s on
short segments at speed, so that still leaves about 4x headroom.

Note what streaming does NOT buy you: RRF queues up to 60 moves ahead, so when
the host sends move N the machine is still executing move N-60. Host-side
timing cannot synchronise anything to better than that. Tool sync belongs in
the planner (an E axis, or a CAN-FD toolboard), not in the host.

    python stream_gcode.py 192.168.0.40 cura_dryrun.gcode
    python stream_gcode.py 192.168.0.40 job.gcode --batch 20 --pre "M220 S150"
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request

BUFF_TOTAL = 250          # board's G-code input buffer, bytes
BUFF_FLOOR = 60           # keep this much free; below it, wait
IDLE_SETTLE = 3           # consecutive idle reads before declaring the job done


class Duet:
    def __init__(self, host: str, timeout: float = 20.0):
        self.base = "http://%s" % host
        self.timeout = timeout
        self.avail = BUFF_TOTAL

    def _get(self, path: str) -> str:
        with urllib.request.urlopen(self.base + path, timeout=self.timeout) as r:
            return r.read().decode("utf-8", "replace")

    def connect(self) -> None:
        self._get("/rr_connect?password=")

    def model(self, key: str):
        raw = self._get("/rr_model?key=" + urllib.parse.quote(key))
        return json.loads(raw).get("result")

    def send(self, payload: str) -> int:
        """Queue one or more newline-separated commands. Returns free buffer."""
        raw = self._get("/rr_gcode?gcode=" + urllib.parse.quote(payload))
        try:
            self.avail = int(json.loads(raw).get("buff", 0))
        except (ValueError, AttributeError):
            self.avail = 0
        return self.avail

    def reply(self) -> str:
        return self._get("/rr_reply").strip()

    def wait_for_space(self, need: int, deadline: float = 30.0) -> bool:
        """Block until the board has room for `need` bytes."""
        start = time.monotonic()
        while self.avail < max(need, BUFF_FLOOR):
            if time.monotonic() - start > deadline:
                return False
            time.sleep(0.02)
            self.send("")          # empty request: refreshes buff, queues nothing
        return True


def stream(duet: Duet, lines: list[str], batch: int, quiet: bool) -> float:
    t0 = time.monotonic()
    sent = 0
    stalls = 0
    last_report = t0

    i = 0
    n = len(lines)
    while i < n:
        # Batch by BYTES, not by line count. The board's input buffer is 250
        # bytes and real G-code lines run ~23 bytes, so a 20-line batch would
        # be ~460 bytes and could never be accepted -- roughly 10 lines fit.
        # (A probe using 4-byte commands makes 20-line batches look fine; it
        # is measuring the wrong thing.)
        chunk, used, j = [], 0, i
        while j < n and len(chunk) < batch:
            cost = len(lines[j].encode("ascii", "replace")) + 1
            if used + cost > BUFF_TOTAL - 1:
                break
            chunk.append(lines[j])
            used += cost
            j += 1
        if not chunk:
            raise ValueError("single line exceeds the %d-byte board buffer: %r"
                             % (BUFF_TOTAL, lines[i][:80]))
        if not duet.wait_for_space(used):
            stalls += 1
            continue
        duet.send("\n".join(chunk))
        i = j
        sent += len(chunk)

        now = time.monotonic()
        if not quiet and now - last_report >= 30:
            last_report = now
            el = now - t0
            pct = 100.0 * sent / len(lines)
            print("  %6.0fs  %5.1f%%  %d/%d lines  %5.0f lines/s  buff %3d  stalls %d"
                  % (el, pct, sent, len(lines), sent / el, duet.avail, stalls))
            sys.stdout.flush()

    feed_done = time.monotonic() - t0
    if not quiet:
        print("  all %d lines fed in %.0fs (%.0f lines/s); waiting for the queue to drain"
              % (sent, feed_done, sent / feed_done))
        sys.stdout.flush()

    idle = 0
    while idle < IDLE_SETTLE:
        time.sleep(1.0)
        idle = idle + 1 if duet.model("state.status") == "idle" else 0
    return time.monotonic() - t0 - IDLE_SETTLE


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("host")
    ap.add_argument("gcode")
    ap.add_argument("--batch", type=int, default=20, help="lines per request (default 20)")
    ap.add_argument("--pre", action="append", default=[],
                    help="command to send before streaming; repeatable")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    with open(args.gcode, encoding="utf-8", errors="replace") as fh:
        lines = [s.split(";")[0].strip() for s in fh]
    lines = [s for s in lines if s]

    duet = Duet(args.host)
    duet.connect()

    status = duet.model("state.status")
    if status != "idle":
        print("machine is %r, not idle -- refusing to stream" % status, file=sys.stderr)
        return 1

    for cmd in args.pre:
        duet.send(cmd)
        time.sleep(0.3)
        print("pre: %-18s -> %s" % (cmd, duet.reply().replace("\n", " | ")))

    print("streaming %d lines to %s, %d per request" % (len(lines), args.host, args.batch))
    elapsed = stream(duet, lines, args.batch, args.quiet)
    print("STREAMED JOB COMPLETE: %.0f s (%dm %02ds)"
          % (elapsed, int(elapsed // 60), int(elapsed % 60)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

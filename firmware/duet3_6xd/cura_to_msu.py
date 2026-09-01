#!/usr/bin/env python3
"""
NOTE (2026-08-30): printer_msu v2's interpreter+emitter now perform this
transform with profile-sourced limits (`printer-msu print/check`); this
script stays as a standalone fallback but its LIMITS dict is hardcoded —
prefer the app for anything the app can do.
Convert slicer G-code into something the MSU machine can run on the Duet.

Handles the two traps that bite silently:

1. **M203 flavour.** Cura emits Marlin-flavour G-code, where M203 is mm/s.
   RepRapFirmware reads M203 as mm/**min**. A file's "M203 X500 Y500 Z10"
   therefore caps X/Y at 8.3 mm/s and Z at 0.17 mm/s -- the machine crawls and
   looks like a hardware fault. M203 is stripped (as are M201/M205, which are
   machine ceilings we have already validated). M204 is KEPT by default,
   because that is the file's actual print/travel acceleration request -- pass
   --drop-m204 to strip it too and let the machine's own M201 govern.

2. **Z is reflected, not offset.** Machine zero is at the TOP (SRS D11), so in
   absolute mode machine_z = z_bed_offset - gcode_z. But Cura's end script does
   its retract-and-raise under G91, where the correct transform is *negation*.
   Getting that wrong drives the tool into the plane on the one move whose
   entire purpose is to escape it.

Also strips heaters, fans and the extruder, since the machine has none yet, and
refuses to emit a file whose envelope falls outside the axis limits.

    python cura_to_msu.py in.gcode out.gcode [--xoff 30] [--yoff 30] [--zbed 100]
"""
from __future__ import annotations

import argparse
import re
import sys

# Axis limits, from M208 in config.g.
LIMITS = {"X": (0.0, 987.448), "Y": (0.0, 290.269), "Z": (0.0, 282.306)}

# Heaters, fans, extruder, and the machine-ceiling commands described above.
DROP = {"M104", "M105", "M106", "M107", "M109", "M140", "M190", "M82", "M83",
        "M84", "M201", "M203", "M205", "M220", "M221", "G92"}

WORD = re.compile(r"([A-Za-z])(-?\d*\.?\d+)")


def convert(src: str, xoff: float, yoff: float, zbed: float) -> tuple[list[str], dict]:
    out = [
        "; converted by cura_to_msu.py",
        "; XY +%g/+%g ; machine_z = %g - gcode_z (machine zero at the TOP, SRS D11)" % (xoff, yoff, zbed),
        "; heaters/fans/extruder stripped; M201/M203/M205 stripped; M204 %s"
        % ("stripped (machine M201 governs)" if "M204" in DROP else "kept"),
        "G21",
        "G90",
    ]
    stats = {"moves": 0, "kept": 0, "dropped": 0}
    relative = False

    with open(src, encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            line = raw.split(";")[0].strip()
            if not line:
                continue
            cmd = line.split()[0].upper()
            if cmd in DROP:
                stats["dropped"] += 1
                continue
            if cmd in ("G90", "G91"):
                relative = cmd == "G91"
                out.append(cmd)
                stats["kept"] += 1
                continue
            if cmd not in ("G0", "G1"):
                out.append(line)
                stats["kept"] += 1
                continue

            parts, has_axis = [cmd], False
            for letter, val in WORD.findall(line[len(cmd):]):
                axis, v = letter.upper(), float(val)
                if axis == "E":
                    continue
                if axis == "X":
                    v = v if relative else v + xoff
                elif axis == "Y":
                    v = v if relative else v + yoff
                elif axis == "Z":
                    v = -v if relative else zbed - v
                elif axis != "F":
                    continue
                if axis != "F":
                    has_axis = True
                parts.append("F%g" % v if axis == "F" else "%s%.3f" % (axis, v))
            if not has_axis and len(parts) == 1:
                stats["dropped"] += 1
                continue
            out.append(" ".join(parts))
            stats["moves"] += 1

    return out, stats


def envelope(lines: list[str]) -> dict:
    """Replay the emitted program to find the real travelled envelope."""
    cur = {"X": 0.0, "Y": 0.0, "Z": 0.0}
    lo: dict[str, float] = {}
    hi: dict[str, float] = {}
    relative = False
    for line in lines:
        if line in ("G90", "G91"):
            relative = line == "G91"
        elif line[:2] in ("G0", "G1"):
            for letter, val in WORD.findall(line[2:]):
                axis = letter.upper()
                if axis not in cur:
                    continue
                cur[axis] = cur[axis] + float(val) if relative else float(val)
                lo[axis] = min(lo.get(axis, cur[axis]), cur[axis])
                hi[axis] = max(hi.get(axis, cur[axis]), cur[axis])
    return {a: (lo.get(a, 0.0), hi.get(a, 0.0)) for a in "XYZ"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--xoff", type=float, default=30.0)
    ap.add_argument("--yoff", type=float, default=30.0)
    ap.add_argument("--zbed", type=float, default=100.0,
                    help="machine Z that corresponds to slicer Z=0 (the bed)")
    ap.add_argument("--drop-m204", action="store_true",
                    help="also strip M204, so the machine's own M201 governs "
                         "acceleration instead of the slicer's request")
    args = ap.parse_args()

    if args.drop_m204:
        DROP.add("M204")
    lines, stats = convert(args.src, args.xoff, args.yoff, args.zbed)
    print("moves %(moves)d  other kept %(kept)d  dropped %(dropped)d" % stats)

    bad = False
    for axis, (lo, hi) in envelope(lines).items():
        amin, amax = LIMITS[axis]
        ok = lo >= amin and hi <= amax
        bad |= not ok
        print("%s  %9.3f .. %9.3f   limits %g..%g   %s"
              % (axis, lo, hi, amin, amax, "OK" if ok else "*** OUT OF RANGE ***"))
    if bad:
        print("refusing to write: envelope falls outside the axis limits", file=sys.stderr)
        return 1

    with open(args.dst, "w", encoding="ascii", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    print("wrote %s" % args.dst)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

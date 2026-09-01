"""V2-M2 hardware shakeout: DuetMotionAdapter against the live board.

Motion-only (no heaters); run from the repo root on the lab machine with the
board reachable at its static IP:

    python firmware/duet3_6xd/shakeout_adapter.py --profile configs/duet_lab.json

Sequence (each step gated by a y/N prompt where it moves):
  1. connect + config drift check (expect CLEAN — else run apply-config)
  2. home all (bare G28 -> board homeall.g, tool-Z first)
  3. small jog square near home on X/Y (adapter G1 path, soft limits armed)
  4. deliberately command a move outside the envelope -> RRF must refuse it
     (adapter surfaces the board's error reply)
  5. M112 e-stop + M999 recovery (board reboots; homed state must be gone)
  6. re-home, park

Writes shakeout_report.json for docs/test-records/.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "libs" / "fmc-motion" / "src"))

from printer_msu.config.profile import load_profile  # noqa: E402
from printer_msu.controllers.motion.duet.client import DuetError  # noqa: E402
from printer_msu.machine import build_machine  # noqa: E402

REPORT: dict = {}


def ask(prompt: str) -> bool:
    return input(f"{prompt} [y/N] ").strip().lower() == "y"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="configs/duet_lab.json")
    ap.add_argument("--out", default="shakeout_report.json")
    args = ap.parse_args()

    profile = load_profile(args.profile)
    machine = build_machine(profile)
    adapter = machine.motion

    print("=== 1. connect + drift check ===")
    adapter.connect()
    REPORT["drift_findings"] = list(adapter.drift_findings)
    if adapter.drift_findings:
        for f in adapter.drift_findings:
            print(f"  DRIFT: {f}")
        print("  -> board disagrees with the profile; run "
              "`printer-msu apply-config` (then M999) before shaking out")
        if not ask("continue anyway?"):
            return 1
    else:
        print("  clean - board matches the profile")

    print("\n=== 2. home all (board homeall.g: tool-Z first) ===")
    if not ask("travel clear, hand near the power switch. Home all?"):
        return 1
    t0 = time.monotonic()
    adapter.home()
    REPORT["home_all_s"] = round(time.monotonic() - t0, 1)
    REPORT["homed"] = sorted(adapter.homed_axes())
    print(f"  homed {REPORT['homed']} in {REPORT['home_all_s']} s")

    print("\n=== 3. jog square near home (X/Y, 20 mm) ===")
    if ask("run the jog square?"):
        t0 = time.monotonic()
        for target in ({"X": 25.0, "Y": 5.0}, {"X": 25.0, "Y": 25.0},
                       {"X": 5.0, "Y": 25.0}, {"X": 5.0, "Y": 5.0}):
            adapter.move_absolute(target, feed=20.0)
        REPORT["jog_square_s"] = round(time.monotonic() - t0, 1)
        pos = adapter.full_status()["axes"]
        REPORT["jog_final"] = {n: a["position"] for n, a in pos.items()}
        print(f"  done in {REPORT['jog_square_s']} s at {REPORT['jog_final']}")

    print("\n=== 4. out-of-envelope move must be refused HOST-SIDE ===")
    # Finding 2026-08-30: RRF M564 S1 silently CLAMPS to M208 instead of
    # rejecting, so the refusal must come from the adapter (and does, since
    # v2 commit after the first shakeout). The board's clamp is the backstop.
    x_max = profile.axis("X").limits_mm[1]
    try:
        adapter.move_absolute({"X": x_max + 50.0}, feed=20.0)
        REPORT["envelope_refusal"] = ("FAILED - accepted! (host-side check "
                                      "missing?)")
        print("  !! ACCEPTED - the adapter's host-side soft-limit check is "
              "not working")
    except DuetError as exc:
        REPORT["envelope_refusal"] = f"refused host-side: {exc}"
        print(f"  refused host-side as expected: {exc}")

    print("\n=== 5. e-stop + recovery ===")
    if ask("send M112 (board halts) then M999 (board reboots)?"):
        adapter.estop()
        status = adapter.get_status()
        REPORT["estop_active"] = status.emergency_active
        print(f"  halted: {status.emergency_active}")
        t0 = time.monotonic()
        adapter.clear_estop()
        REPORT["m999_recovery_s"] = round(time.monotonic() - t0, 1)
        REPORT["homed_after_reset"] = sorted(adapter.homed_axes())
        print(f"  back in {REPORT['m999_recovery_s']} s; homed axes after "
              f"reset: {REPORT['homed_after_reset']} (expect [])")

        print("\n=== 6. re-home + park ===")
        if ask("re-home all?"):
            adapter.home()
            adapter.move_absolute({"X": 5.0, "Y": 5.0}, feed=20.0)
            REPORT["rehomed"] = sorted(adapter.homed_axes())

    adapter.disconnect()
    Path(args.out).write_text(json.dumps(REPORT, indent=2), encoding="utf-8")
    print(f"\nreport written to {args.out}")
    print(json.dumps(REPORT, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

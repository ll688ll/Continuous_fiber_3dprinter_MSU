"""One-time migration: config.g axis sections -> M98 include (v2 decision D6).

Moves the axis configuration's source of truth from the hand-edited config.g
on the SD card to the machine profile + generated /sys/msu_machine.g.

Order of operations is chosen so the board can NEVER boot without axis
config, and nothing is applied unless every byte verified:

  1. download the card's current /sys/config.g; require it to match the
     expected pre-migration content (git HEAD copy, line endings normalized)
     - aborts if the card has drifted from the repo;
  2. back it up (locally + /sys/config.g.premsu.bak on the card);
  3. upload the GENERATED /sys/msu_machine.g first and verify byte-exact
     (a failure here leaves the old, self-contained config.g untouched);
  4. upload the new include-style config.g and verify byte-exact;
  5. reboot (M999), reconnect, and run the connect-time drift check with a
     fresh adapter - it must read clean.

Usage (lab machine):  python firmware/duet3_6xd/migrate_config_include.py
                          [--profile configs/duet_lab.json] [--yes]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "libs" / "fmc-motion" / "src"))

from printer_msu.config.duet_config_gen import BOARD_PATH, generate_machine_g  # noqa: E402
from printer_msu.config.profile import CONTROLLER_DUET, load_profile  # noqa: E402
from printer_msu.controllers.motion.duet.adapter import DuetMotionAdapter  # noqa: E402
from printer_msu.controllers.motion.duet.client import DuetClient  # noqa: E402

CONFIG_LOCAL = REPO / "firmware" / "duet3_6xd" / "config.g"
BACKUP_DIR = REPO / "docs" / "test-records" / "2026-08-30"
CARD_BACKUP = "/sys/config.g.premsu.bak"


def norm(data: bytes) -> bytes:
    """Line-ending-insensitive comparison form (card may hold CRLF or LF)."""
    return data.replace(b"\r\n", b"\n").strip()


def git_head_config() -> bytes:
    out = subprocess.run(
        ["git", "show", "HEAD:firmware/duet3_6xd/config.g"],
        cwd=REPO, capture_output=True, check=True)
    return out.stdout


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default=str(REPO / "configs" / "duet_lab.json"))
    ap.add_argument("--yes", action="store_true",
                    help="skip the reboot confirmation prompt")
    args = ap.parse_args()

    profile = load_profile(args.profile)
    duet = next(c for c in profile.controllers if c.type == CONTROLLER_DUET)
    client = DuetClient(duet.connection["host"],
                        password=duet.connection.get("password", ""),
                        timeout_s=float(duet.connection.get("http_timeout_s", 20)))
    client.connect()
    print(f"connected to {duet.connection['host']}")

    # 1. card config.g must match the expected pre-migration content
    card = client.download("/sys/config.g")
    expected_old = git_head_config()
    new_config = CONFIG_LOCAL.read_bytes()
    if norm(card) == norm(new_config):
        print("card config.g already carries the M98 include - nothing to do")
        migrated_already = True
    elif norm(card) != norm(expected_old):
        print("ABORT: the card's config.g matches neither the expected "
              "pre-migration version (git HEAD) nor the new include version.")
        print("Someone edited the card outside the repo. Reconcile by hand "
              "first; the card content was saved locally for inspection.")
        (BACKUP_DIR / "config.g.card-unexpected").write_bytes(card)
        return 1
    else:
        migrated_already = False

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    if not migrated_already:
        # 2. back up the current card file, locally and on the card
        (BACKUP_DIR / "config.g.before-include").write_bytes(card)
        client.upload(CARD_BACKUP, card)
        assert client.download(CARD_BACKUP) == card
        print(f"backed up card config.g -> {CARD_BACKUP} and "
              f"{BACKUP_DIR / 'config.g.before-include'}")

        # 3. generated include FIRST (old config.g stays self-contained)
        machine_g = generate_machine_g(profile).encode("ascii")
        client.upload(BOARD_PATH, machine_g)
        assert client.download(BOARD_PATH) == machine_g, "verify failed"
        print(f"uploaded + verified {BOARD_PATH} ({len(machine_g)} bytes)")

        # 4. the new include-style config.g (normalized to LF/ASCII)
        new_lf = norm(new_config) + b"\n"
        new_lf.decode("ascii")  # raises if anything non-ASCII slipped in
        client.upload("/sys/config.g", new_lf)
        assert client.download("/sys/config.g") == new_lf, "verify failed"
        print(f"uploaded + verified /sys/config.g ({len(new_lf)} bytes)")

    # 5. reboot + drift check
    if not args.yes:
        answer = input("reboot the board now to apply (M999)? [y/N] ")
        if answer.strip().lower() != "y":
            print("not rebooted - the new config takes effect on next boot")
            return 0
    print("rebooting (M999)...")
    try:
        client.send_emergency("M999")
    except Exception:
        pass  # the connection dropping mid-reboot is expected
    deadline = time.monotonic() + 90.0
    fresh = None
    t0 = time.monotonic()
    while time.monotonic() < deadline:
        time.sleep(3.0)
        try:
            fresh = DuetClient(duet.connection["host"],
                               password=duet.connection.get("password", ""))
            fresh.connect()
            break
        except Exception:
            fresh = None
    if fresh is None:
        print("board did not come back within 90 s - check power/network; "
              f"the pre-migration config is on the card at {CARD_BACKUP}")
        return 1
    print(f"back in {time.monotonic() - t0:.1f} s")

    adapter = DuetMotionAdapter(fresh, profile.axes_on("duet"))
    adapter.connect()
    report = {
        "migrated": not migrated_already,
        "drift_findings": adapter.drift_findings,
        "card_backup": CARD_BACKUP,
    }
    out = BACKUP_DIR / "config_include_migration.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    if adapter.drift_findings:
        print("DRIFT AFTER MIGRATION (board != profile):")
        for f in adapter.drift_findings:
            print(f"  ! {f}")
        print(f"restore with: upload {CARD_BACKUP} back to /sys/config.g")
        return 1
    print("drift check CLEAN - the profile now drives the board config.")
    print(f"report written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

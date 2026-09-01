"""Command-line interface (printer PRD FR-CLI-01..03).

Subcommands:
- ``check <file> --profile <p>``  parse + interpret + preflight; exit 0 = pass
- ``print <file> --profile <p>``  stream the file (sim or real); exit 0 = completed
- ``ui``                          launch the (placeholder) PySide6 window

stdlib only; PySide6 stays an optional extra.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as _dt
import logging
import sys
import time
from pathlib import Path
from typing import Optional, Sequence

from .config.profile import CONTROLLER_FMC, MachineProfile, load_profile
from .core.gcode.analysis import analyze
from .core.gcode.interpreter import interpret
from .core.gcode.parser import parse_file
from .core.model.print_job import PrintJobState, TERMINAL_STATES
from .errors import PrinterError
from .services.print_service import (
    JobEvent,
    PrintJobRunner,
    run_preflight,
)

logger = logging.getLogger("printer_msu.cli")

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_PREFLIGHT = 2
EXIT_USAGE = 3


def _load_profile(args) -> MachineProfile:
    profile = load_profile(args.profile)
    if getattr(args, "mode", None):
        if profile.motion is None:
            raise PrinterError(
                "--mode only applies to fmc_motion profiles; this profile's "
                "motion controller is "
                f"{profile.motion_controller.type!r}")
        if args.mode != profile.motion.mode:
            controllers = tuple(
                dataclasses.replace(
                    c, connection={**dict(c.connection), "mode": args.mode})
                if c.type == CONTROLLER_FMC else c
                for c in profile.controllers)
            profile = dataclasses.replace(
                profile,
                controllers=controllers,
                motion=dataclasses.replace(profile.motion, mode=args.mode))
    return profile


def _build_adapter(profile: MachineProfile):
    from .machine import build_adapter  # deferred: keeps `--help` fast

    return build_adapter(profile)


# -------------------------------------------------------------------- check
def _cmd_check(args) -> int:
    profile = _load_profile(args)
    lines = parse_file(args.file)
    usage = analyze(lines)
    result = interpret(lines, profile)

    adapter = _build_adapter(profile)
    offline = False
    try:
        adapter.connect()
    except Exception as exc:
        offline = True
        print(f"note: controller not reachable ({exc}); file-level checks only")
    try:
        report = run_preflight(profile, adapter, result,
                               home_first=args.home_first, offline=offline)
    finally:
        if not offline:
            adapter.disconnect()

    print(usage.summary())
    if result.skipped:
        tallies = ", ".join(f"{code} x{n}"
                            for code, n in sorted(result.skipped.items()))
        print(f"skipped (motion-only v1): {tallies}")
    for finding in report.findings:
        print(str(finding))
    print(f"preflight: {'PASS' if report.passed else 'FAIL'}")
    return EXIT_OK if report.passed else EXIT_PREFLIGHT


# -------------------------------------------------------------------- print
class _ConsoleProgress:
    """Single-line console progress; state changes on their own lines."""

    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        self._last_render = 0.0

    def __call__(self, event: JobEvent) -> None:
        if event.kind == "progress":
            now = time.monotonic()
            if now - self._last_render < 0.1 and event.percent < 100.0:
                return
            self._last_render = now
            sys.stdout.write(
                f"\r[{self.job_id}] {event.percent:5.1f}% "
                f"(action {event.action_index}/{event.action_total}, "
                f"line {event.line_no})   ")
            sys.stdout.flush()
        elif event.kind == "state":
            sys.stdout.write(f"\n[{self.job_id}] {event.state.value.upper()}\n")
            sys.stdout.flush()
        elif event.kind in ("warning", "error"):
            sys.stdout.write(f"\n[{self.job_id}] {event.kind}: {event.message}\n")
            sys.stdout.flush()


def _cmd_print(args) -> int:
    profile = _load_profile(args)
    job_id = _dt.datetime.now().strftime("job-%Y%m%d-%H%M%S")
    report_path = Path(args.report) if args.report else Path("logs") / f"{job_id}.json"
    log_path = report_path.with_suffix(".log")
    _attach_job_log(log_path)

    lines = parse_file(args.file)
    result = interpret(lines, profile)

    adapter = _build_adapter(profile)
    adapter.connect()
    try:
        report = run_preflight(profile, adapter, result,
                               home_first=args.home_first)
        for finding in report.findings:
            print(str(finding))
        if not report.passed:
            print("preflight: FAIL — not starting")
            return EXIT_PREFLIGHT

        runner = PrintJobRunner(profile, adapter, result, args.file,
                                job_id=job_id, report_path=report_path,
                                home_first=args.home_first)
        runner.subscribe(_ConsoleProgress(job_id))
        print(f"[{job_id}] {profile.profile_name} "
              f"({profile.mode_label}) - Ctrl-C stops, twice = e-stop")
        runner.start()
        _run_print_with_interrupts(runner)
        return EXIT_OK if runner.state is PrintJobState.COMPLETED else EXIT_FAILED
    finally:
        adapter.disconnect()


def _run_print_with_interrupts(runner: PrintJobRunner) -> None:
    """Join the worker; first Ctrl-C stops, second e-stops (FR-CLI-02)."""
    interrupts = 0
    while runner.state not in TERMINAL_STATES:
        try:
            if runner.join(timeout=0.2):
                break
        except KeyboardInterrupt:
            interrupts += 1
            if interrupts == 1:
                print("\nCtrl-C: stopping (again for e-stop)")
                runner.stop()
            else:
                print("\nCtrl-C x2: E-STOP")
                runner.estop()
    runner.join(timeout=10.0)


def _attach_job_log(log_path: Path) -> None:
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(log_path, encoding="utf-8")
    except OSError:
        return
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"))
    for name in ("printer_msu", "fmc_motion"):
        namespace_logger = logging.getLogger(name)
        namespace_logger.addHandler(handler)
        if namespace_logger.level in (logging.NOTSET, logging.WARNING):
            namespace_logger.setLevel(logging.DEBUG)


# -------------------------------------------------------------- apply-config
def _cmd_apply_config(args) -> int:
    """Generate the board include from the profile; upload on request (D6)."""
    from .config.duet_config_gen import BOARD_PATH, generate_machine_g
    from .config.profile import CONTROLLER_DUET

    profile = load_profile(args.profile)
    text = generate_machine_g(profile)
    print(f"; generated for {BOARD_PATH}:")
    print(text)
    if args.dry_run:
        return EXIT_OK

    duet = next((c for c in profile.controllers if c.type == CONTROLLER_DUET),
                None)
    if duet is None:
        raise PrinterError("profile has no duet controller to apply to")
    from .controllers.motion.duet.client import DuetClient

    client = DuetClient(duet.connection["host"],
                        password=duet.connection.get("password", ""),
                        timeout_s=float(duet.connection.get("http_timeout_s", 20)))
    client.connect()
    client.upload(BOARD_PATH, text.encode("ascii"))
    print(f"uploaded {BOARD_PATH} (CRC-verified)")
    print("NOTE: config.g must contain  M98 P\"/sys/msu_machine.g\"  once "
          "(one-time manual edit),")
    print("      and the board needs a reset (M999) to apply changes: "
          "send it now? [y/N] ", end="", flush=True)
    if input().strip().lower() == "y":
        try:
            client.send_emergency("M999")
        except Exception:
            pass  # reset kills the HTTP reply mid-flight; expected
        print("M999 sent - board is rebooting (a few seconds); re-home "
              "before moving")
    else:
        print("skipped - changes apply after the next power cycle/M999")
    return EXIT_OK


# ----------------------------------------------------------------- calibrate
def _cmd_calibrate_plane(args) -> int:
    """Jog the tool's Z down to touch the print plane; capture the offset."""
    from .config.profile_store import save_profile
    import dataclasses as _dc

    profile = load_profile(args.profile)
    tool = profile.tool(args.tool)
    if tool is None or tool.z_axis is None:
        raise PrinterError(f"tool {args.tool} has no z_axis configured")
    z_axis = tool.z_axis

    adapter = _build_adapter(profile)
    adapter.connect()
    try:
        drift = getattr(adapter, "drift_findings", None) or []
        for f in drift:
            print(f"[drift] {f}")
        if z_axis not in adapter.homed_axes():
            print(f"homing {z_axis}...")
            adapter.home([z_axis])
        pos = adapter.full_status()["axes"][z_axis]["position"]
        print(f"\n{z_axis} at {pos:.3f} mm (machine zero is at the TOP; "
              f"larger = closer to the plane).")
        print("Jog until the tool touches the print plane, then capture:")
        print("  +<mm> / -<mm>   jog (step clamped to 5 mm, e.g. +1, -0.1)")
        print("  cap             capture the plane offset into the profile")
        print("  quit            abort without saving")
        while True:
            try:
                cmd = input(f"[{z_axis} {pos:.3f}] > ").strip().lower()
            except EOFError:
                return EXIT_FAILED
            if cmd in ("quit", "q", "abort"):
                print("aborted - profile unchanged")
                return EXIT_FAILED
            if cmd == "cap":
                new_tools = tuple(
                    _dc.replace(t, plane_offset_mm=round(pos, 3))
                    if t.tool_index == args.tool else t
                    for t in profile.tools)
                new_profile = _dc.replace(profile, tools=new_tools)
                save_profile(args.profile, new_profile)
                print(f"tool {args.tool}: plane_offset_mm = {pos:.3f} saved "
                      f"to {args.profile}")
                return EXIT_OK
            try:
                step = float(cmd)
            except ValueError:
                print("commands: +<mm>, -<mm>, cap, quit")
                continue
            step = max(-5.0, min(5.0, step))
            adapter.move_relative({z_axis: step},
                                  feed=args.feed_mm_s)
            pos = adapter.full_status()["axes"][z_axis]["position"]
    finally:
        adapter.disconnect()


# ----------------------------------------------------------------------- ui
def _cmd_ui(args) -> int:
    from .app import main as ui_main

    forwarded: list[str] = []
    if args.profile:
        forwarded += ["--profile", args.profile]
    if args.file:
        forwarded.append(args.file)
    return ui_main(forwarded)


# --------------------------------------------------------------------- main
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="printer-msu",
        description="Continuous-fiber printer control (motion-only v1)")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p) -> None:
        p.add_argument("file", help="G-code file")
        p.add_argument("--profile", required=True, help="machine profile JSON")
        p.add_argument("--mode", choices=["sim", "real"],
                       help="override the profile's motion mode")
        p.add_argument("--home-first", action="store_true",
                       help="home all mapped axes before streaming")
        p.add_argument("--log-level", default="INFO",
                       choices=["DEBUG", "INFO", "WARNING", "ERROR"])

    check = sub.add_parser("check", help="parse + validate + preflight a file")
    add_common(check)
    check.set_defaults(func=_cmd_check)

    prnt = sub.add_parser("print", help="stream a file to the machine")
    add_common(prnt)
    prnt.add_argument("--report", help="job report path (default logs/<job>.json)")
    prnt.set_defaults(func=_cmd_print)

    apply_cfg = sub.add_parser(
        "apply-config",
        help="generate the board config include from the profile; upload it")
    apply_cfg.add_argument("--profile", required=True)
    apply_cfg.add_argument("--dry-run", action="store_true",
                           help="print the generated file, upload nothing")
    apply_cfg.set_defaults(func=_cmd_apply_config, log_level="INFO")

    calibrate = sub.add_parser(
        "calibrate", help="interactive calibration workflows")
    calibrate_sub = calibrate.add_subparsers(dest="what", required=True)
    plane = calibrate_sub.add_parser(
        "plane", help="jog tool Z to the print plane; save plane_offset_mm")
    plane.add_argument("--profile", required=True)
    plane.add_argument("--tool", type=int, default=0)
    plane.add_argument("--feed-mm-s", type=float, default=5.0,
                       help="jog feed (mm/s, default 5)")
    plane.set_defaults(func=_cmd_calibrate_plane, log_level="INFO")

    ui = sub.add_parser("ui", help="launch the PySide6 print console")
    ui.add_argument("file", nargs="?", help="G-code file to preload")
    ui.add_argument("--profile", help="machine profile JSON to preload")
    ui.set_defaults(func=_cmd_ui)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    level = getattr(args, "log_level", "INFO")
    logging.basicConfig(level=level,
                        format="%(levelname)s %(name)s: %(message)s")
    # the job-file handler (DEBUG) forces namespace loggers below the root
    # level; the console handler must filter on its own level or it would
    # echo every propagated DEBUG record
    for handler in logging.getLogger().handlers:
        handler.setLevel(level)
    try:
        return args.func(args)
    except PrinterError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())

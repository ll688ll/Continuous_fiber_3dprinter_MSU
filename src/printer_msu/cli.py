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

from .config.profile import MachineProfile, load_profile
from .controllers.motion.fmc_motion_adapter import FmcMotionAdapter
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
    if getattr(args, "mode", None) and args.mode != profile.motion.mode:
        profile = dataclasses.replace(
            profile,
            motion=dataclasses.replace(profile.motion, mode=args.mode))
    return profile


def _build_adapter(profile: MachineProfile) -> FmcMotionAdapter:
    from fmc_motion import create_controller  # deferred: keeps `--help` fast

    controller = create_controller(str(profile.motion.config_path),
                                   mode=profile.motion.mode)
    return FmcMotionAdapter(controller, axis_map=profile.motion.axis_map)


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
              f"({profile.motion.mode}) - Ctrl-C stops, twice = e-stop")
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


# ----------------------------------------------------------------------- ui
def _cmd_ui(_args) -> int:
    from .app import main as ui_main

    return ui_main([])


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

    ui = sub.add_parser("ui", help="launch the PySide6 window (placeholder)")
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

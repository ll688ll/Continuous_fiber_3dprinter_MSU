"""Frozen-app entry point (PyInstaller). One script, two executables.

``printer_msu.spec`` builds ``printer-msu.exe`` (console) and
``printer-msu-ui.exe`` (windowed) from this same script. Which one is running
decides whether the ``ui`` subcommand is implied, so

    printer-msu-ui.exe --profile configs/duet_lab.json job.gcode

equals

    printer-msu.exe ui --profile configs/duet_lab.json job.gcode
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _windowed_streams() -> None:
    """A windowed exe has no console, so ``sys.stdout``/``sys.stderr`` are
    ``None``. Keep prints harmless and send stderr (start-up tracebacks,
    argparse errors) to a file next to the job reports, so a silent failure
    is still diagnosable."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        try:
            from printer_msu.paths import default_report_dir

            log_dir = default_report_dir()
            log_dir.mkdir(parents=True, exist_ok=True)
            sys.stderr = open(log_dir / "printer-msu-ui.stderr.log", "a",
                              encoding="utf-8")
        except Exception:
            sys.stderr = open(os.devnull, "w", encoding="utf-8")


def main() -> int:
    _windowed_streams()
    from printer_msu.cli import main as cli_main

    argv = list(sys.argv[1:])
    if Path(sys.executable).stem.lower().endswith("-ui"):
        argv = ["ui", *argv]
    return cli_main(argv)


if __name__ == "__main__":
    sys.exit(main())

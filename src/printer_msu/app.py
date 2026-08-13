from __future__ import annotations

import argparse
import sys
from typing import Optional, Sequence


def main(argv: Optional[Sequence[str]] = None) -> int:
    """UI entry point (``printer-msu ui``).

    Keeps imports lightweight so the package can be imported/tested without
    PySide6 (it is the ``[ui]`` extra).
    """
    parser = argparse.ArgumentParser(prog="printer-msu ui",
                                     description="PySide6 print console")
    parser.add_argument("file", nargs="?", help="G-code file to preload")
    parser.add_argument("--profile", help="machine profile JSON to preload")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    try:
        from PySide6.QtWidgets import QApplication
    except Exception as e:  # pragma: no cover
        print("PySide6 is required to run the UI: pip install -e .[ui]")
        print(f"Import error: {e}")
        return 1

    from .ui.print_console import PrintConsoleWindow

    app = QApplication([])
    window = PrintConsoleWindow(profile_path=args.profile, gcode_path=args.file)
    window.show()
    return app.exec()

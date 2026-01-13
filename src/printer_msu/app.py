from __future__ import annotations

import sys
from typing import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    """
    Application entry point.

    Keeps imports lightweight so the package can be imported/tested without PySide6.
    """
    _ = argv if argv is not None else sys.argv[1:]

    try:
        from PySide6.QtWidgets import QApplication
    except Exception as e:  # pragma: no cover
        print("PySide6 is required to run the UI.")
        print(f"Import error: {e}")
        return 1

    from .ui.main_window import MainWindow

    app = QApplication([])
    w = MainWindow()
    w.show()
    return app.exec()



from __future__ import annotations

try:  # Allow importing this module without PySide6 installed (e.g., during non-UI unit tests).
    from PySide6.QtWidgets import QMainWindow, QWidget
except Exception:  # pragma: no cover
    QMainWindow = object  # type: ignore
    QWidget = object  # type: ignore


class MainWindow(QMainWindow):
    """Main application window (placeholder)."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("printer_msu")

        # Placeholder central widget so the window can render.
        try:
            self.setCentralWidget(QWidget())
        except Exception:
            pass



"""Qt bridge: worker-thread events -> UI-thread signals (PRD M4, FR-JOB-05).

Everything here is emitted from non-UI threads; Qt's auto-connection queues
cross-thread signal deliveries onto the receiver's (UI) thread, which is the
whole point — the UI never blocks on controller I/O (SRS UI-010).
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

from PySide6.QtCore import QObject, Signal

from ..services.print_service import JobEvent


class JobBridge(QObject):
    """Adapts ``PrintJobRunner`` observer callbacks to Qt signals."""

    state_changed = Signal(str)
    progress = Signal(int, int, int, float)  # index, total, line_no, percent
    warning = Signal(str)
    error = Signal(str)

    def dispatch(self, event: JobEvent) -> None:  # called from the job worker
        if event.kind == "state" and event.state is not None:
            self.state_changed.emit(event.state.value)
        elif event.kind == "progress":
            self.progress.emit(event.action_index, event.action_total,
                               event.line_no, event.percent)
        elif event.kind == "warning":
            message = (f"line {event.line_no}: {event.message}"
                       if event.line_no else event.message)
            self.warning.emit(message)
        elif event.kind == "error":
            self.error.emit(event.message)


class _LogEmitter(QObject):
    line = Signal(str)


class UiLogHandler(logging.Handler):
    """Feeds the printer/motion log namespaces into the UI log tail."""

    NAMESPACES = ("printer_msu", "fmc_motion")

    def __init__(self, level: int = logging.INFO) -> None:
        super().__init__(level)
        self.emitter = _LogEmitter()
        self.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s",
            datefmt="%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.emitter.line.emit(self.format(record))
        except Exception:  # pragma: no cover - never break logging
            pass

    def attach(self) -> None:
        for name in self.NAMESPACES:
            log = logging.getLogger(name)
            log.addHandler(self)
            if log.level in (logging.NOTSET, logging.WARNING):
                log.setLevel(logging.INFO)

    def detach(self) -> None:
        for name in self.NAMESPACES:
            logging.getLogger(name).removeHandler(self)


class StatusPoller(QObject):
    """Background status polling; emits adapter.full_status() snapshots."""

    status = Signal(object)   # dict from FmcMotionAdapter.full_status()
    failed = Signal(str)

    def __init__(self, adapter, interval_s: float = 0.5,
                 parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._adapter = adapter
        self._interval = interval_s
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop,
                                        name="ui-status-poll", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.status.emit(self._adapter.full_status())
            except Exception as exc:  # keep polling; may be transient
                self.failed.emit(str(exc))
            self._stop.wait(self._interval)


class TaskRunner(QObject):
    """Run blocking controller operations off the UI thread.

    ``finished(name, result, error)`` is emitted (queued) on completion;
    ``error`` is None on success, else a display string.
    """

    finished = Signal(str, object, object)

    def run(self, name: str, fn: Callable[[], object]) -> None:
        def _target() -> None:
            try:
                result = fn()
            except Exception as exc:
                self.finished.emit(name, None, f"{type(exc).__name__}: {exc}")
            else:
                self.finished.emit(name, result, None)

        threading.Thread(target=_target, name=f"ui-task-{name}",
                         daemon=True).start()

"""Minimal print console (printer PRD M4; SRS UI-003 subset, motion-only).

One window: load a machine profile, connect, open a G-code file, check it,
run it — start / pause / resume / stop / e-stop — with a progress bar, live
axis positions, and a log tail. All controller I/O runs off the UI thread
(TaskRunner / StatusPoller / PrintJobRunner); the UI only consumes queued
signals (SRS UI-010).
"""

from __future__ import annotations

import datetime as _dt
import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..config.profile import MachineProfile, load_profile
from ..core.gcode.analysis import analyze
from ..core.gcode.interpreter import interpret
from ..core.gcode.parser import parse_file
from ..core.model.print_job import PrintJobState, TERMINAL_STATES
from ..machine import build_adapter
from ..services.print_service import PrintJobRunner, run_preflight
from .bridge import JobBridge, StatusPoller, TaskRunner, UiLogHandler
from .setup_page import SetupPage

logger = logging.getLogger("printer_msu.ui")

_STATE_COLORS = {
    "running": "#1a7f37", "paused": "#b58900", "completed": "#1a7f37",
    "stopped": "#b34700", "error": "#c0392b",
}


class PrintConsoleWindow(QMainWindow):
    def __init__(self, profile_path: Optional[str] = None,
                 gcode_path: Optional[str] = None) -> None:
        super().__init__()
        self.setWindowTitle("printer_msu — print console (motion-only v1)")
        self.resize(900, 640)

        self.profile: Optional[MachineProfile] = None
        self.profile_path: Optional[str] = None
        self.adapter = None
        self.runner: Optional[PrintJobRunner] = None
        self.gcode_path: Optional[Path] = None
        self.report_dir = Path("logs")
        self._estop_active = False

        self.tasks = TaskRunner(self)
        self.tasks.finished.connect(self._on_task_finished)
        self.bridge = JobBridge(self)
        self.bridge.state_changed.connect(self._on_job_state)
        self.bridge.progress.connect(self._on_progress)
        self.bridge.warning.connect(lambda m: self._log(f"warning: {m}"))
        self.bridge.error.connect(lambda m: self._log(f"ERROR: {m}"))
        self.poller: Optional[StatusPoller] = None

        self._build_ui()

        self.log_handler = UiLogHandler()
        self.log_handler.emitter.line.connect(self._append_log_line)
        self.log_handler.attach()

        if profile_path:
            self.load_profile(profile_path)
        if gcode_path:
            self.open_gcode(gcode_path)
        self._update_buttons()

    # ------------------------------------------------------------------- ui
    def _build_ui(self) -> None:
        self.tabs = QTabWidget()
        central = QWidget()
        root = QVBoxLayout(central)

        # machine row
        machine_box = QGroupBox("Machine")
        grid = QGridLayout(machine_box)
        self.profile_edit = QLineEdit()
        self.profile_edit.setPlaceholderText("machine profile JSON…")
        browse_profile = QPushButton("Browse…")
        browse_profile.clicked.connect(self._browse_profile)
        self.load_profile_btn = QPushButton("Load")
        self.load_profile_btn.clicked.connect(
            lambda: self.load_profile(self.profile_edit.text()))
        self.profile_label = QLabel("no profile loaded")
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self._toggle_connection)
        self.conn_label = QLabel("disconnected")
        grid.addWidget(self.profile_edit, 0, 0)
        grid.addWidget(browse_profile, 0, 1)
        grid.addWidget(self.load_profile_btn, 0, 2)
        grid.addWidget(self.profile_label, 1, 0)
        grid.addWidget(self.connect_btn, 1, 1)
        grid.addWidget(self.conn_label, 1, 2)
        root.addWidget(machine_box)

        # file row
        file_box = QGroupBox("G-code")
        fgrid = QGridLayout(file_box)
        self.file_label = QLabel("no file loaded")
        open_btn = QPushButton("Open…")
        open_btn.clicked.connect(self._browse_gcode)
        self.usage_label = QLabel("")
        self.check_btn = QPushButton("Check")
        self.check_btn.clicked.connect(self.check_file)
        self.check_label = QLabel("")
        fgrid.addWidget(self.file_label, 0, 0)
        fgrid.addWidget(open_btn, 0, 1)
        fgrid.addWidget(self.usage_label, 1, 0)
        fgrid.addWidget(self.check_btn, 1, 1)
        fgrid.addWidget(self.check_label, 1, 2)
        self.capability_view = QPlainTextEdit()
        self.capability_view.setReadOnly(True)
        self.capability_view.setMaximumBlockCount(200)
        self.capability_view.setFixedHeight(72)
        self.capability_view.setPlaceholderText(
            "capability scan: open a file with a profile loaded")
        fgrid.addWidget(self.capability_view, 2, 0, 1, 3)
        root.addWidget(file_box)

        # job controls
        job_box = QGroupBox("Job")
        jrow = QHBoxLayout(job_box)
        self.home_first_check = QCheckBox("Home first")
        self.start_btn = QPushButton("Start")
        self.start_btn.clicked.connect(self.start_print)
        self.pause_btn = QPushButton("Pause")
        self.pause_btn.clicked.connect(self.pause_print)
        self.resume_btn = QPushButton("Resume")
        self.resume_btn.clicked.connect(self.resume_print)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(self.stop_print)
        self.estop_btn = QPushButton("E-STOP")
        self.estop_btn.setStyleSheet(
            "background-color:#c0392b;color:white;font-weight:bold;")
        self.estop_btn.clicked.connect(self.emergency_stop)
        self.clear_estop_btn = QPushButton("Clear E-Stop")
        self.clear_estop_btn.clicked.connect(self.clear_estop)
        for w in (self.home_first_check, self.start_btn, self.pause_btn,
                  self.resume_btn, self.stop_btn, self.estop_btn,
                  self.clear_estop_btn):
            jrow.addWidget(w)
        root.addWidget(job_box)

        # progress
        prow = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.state_label = QLabel("idle")
        self.line_label = QLabel("")
        prow.addWidget(self.progress_bar, 3)
        prow.addWidget(self.state_label, 1)
        prow.addWidget(self.line_label, 2)
        root.addLayout(prow)

        # status + log
        splitter = QSplitter(Qt.Orientation.Horizontal)
        status_box = QGroupBox("Axes")
        svl = QVBoxLayout(status_box)
        self.axis_table = QTableWidget(0, 4)
        self.axis_table.setHorizontalHeaderLabels(
            ["Axis", "Position (mm)", "Speed (mm/s)", "Homed"])
        self.axis_table.verticalHeader().setVisible(False)
        self.axis_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.estop_label = QLabel("")
        svl.addWidget(self.axis_table)
        svl.addWidget(self.estop_label)
        log_box = QGroupBox("Log")
        lvl = QVBoxLayout(log_box)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(2000)
        lvl.addWidget(self.log_view)
        splitter.addWidget(status_box)
        splitter.addWidget(log_box)
        splitter.setSizes([380, 520])
        root.addWidget(splitter, 1)

        self.tabs.addTab(central, "Print")
        self.setup_page = SetupPage(self)
        self.setup_page.profile_saved.connect(self.load_profile)
        self.tabs.addTab(self.setup_page, "Setup")
        self.setCentralWidget(self.tabs)

    # ----------------------------------------------------------- profile/file
    def _browse_profile(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open machine profile",
                                              "configs", "JSON (*.json)")
        if path:
            self.profile_edit.setText(path)
            self.load_profile(path)

    def load_profile(self, path: str) -> None:
        if not path:
            return
        try:
            self.profile = load_profile(path)
        except Exception as exc:
            self._log(f"profile load failed: {exc}")
            QMessageBox.warning(self, "Profile", str(exc))
            return
        self.profile_path = path
        self.profile_edit.setText(path)
        self.profile_label.setText(
            f"{self.profile.profile_name} — mode: {self.profile.mode_label}, "
            f"axes: {', '.join(self.profile.axis_names)}")
        self._log(f"profile loaded: {self.profile.profile_name}")
        self.setup_page.load_from(self.profile, path)
        if self.gcode_path is not None:
            self._scan_capabilities()
        self._update_buttons()

    def _browse_gcode(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open G-code", "examples",
                                              "G-code (*.gcode *.nc *.gc);;All (*)")
        if path:
            self.open_gcode(path)

    def open_gcode(self, path: str) -> None:
        self.gcode_path = Path(path)
        self.file_label.setText(str(path))
        self.check_label.setText("")
        self.tasks.run("parse", lambda: analyze(parse_file(path)))
        if self.profile is not None:
            self._scan_capabilities()
        self._update_buttons()

    # ------------------------------------------------------------ connection
    def _toggle_connection(self) -> None:
        if self.adapter is None:
            self.connect_machine()
        else:
            self.disconnect_machine()

    def connect_machine(self) -> None:
        if self.profile is None:
            return
        profile = self.profile
        self.connect_btn.setEnabled(False)
        self.conn_label.setText("connecting…")

        def _connect():
            adapter = build_adapter(profile)
            adapter.connect()
            return adapter

        self.tasks.run("connect", _connect)

    def disconnect_machine(self) -> None:
        adapter, self.adapter = self.adapter, None
        if self.poller is not None:
            self.poller.stop()
            self.poller = None
        if self.runner is not None and self.runner.state not in TERMINAL_STATES:
            self.runner.stop()
        self.conn_label.setText("disconnecting…")
        self.connect_btn.setEnabled(False)
        self.tasks.run("disconnect",
                       lambda: adapter.disconnect() if adapter else None)

    # ------------------------------------------------------------------- job
    def check_file(self) -> None:
        if not (self.profile and self.adapter and self.gcode_path):
            return
        profile, adapter, path = self.profile, self.adapter, self.gcode_path
        home_first = self.home_first_check.isChecked()

        def _check():
            result = interpret(parse_file(path), profile)
            report = run_preflight(profile, adapter, result,
                                   home_first=home_first)
            return result, report

        self.check_btn.setEnabled(False)
        self.tasks.run("check", _check)

    def start_print(self) -> None:
        if not (self.profile and self.adapter and self.gcode_path):
            return
        if self.runner is not None and self.runner.state not in TERMINAL_STATES:
            return
        profile, adapter, path = self.profile, self.adapter, self.gcode_path
        home_first = self.home_first_check.isChecked()

        def _prepare():
            result = interpret(parse_file(path), profile)
            report = run_preflight(profile, adapter, result,
                                   home_first=home_first)
            return result, report

        self.start_btn.setEnabled(False)
        self.tasks.run("start", _prepare)

    def pause_print(self) -> None:
        if self.runner is not None:
            self.runner.pause()

    def resume_print(self) -> None:
        if self.runner is not None:
            self.runner.resume()

    def stop_print(self) -> None:
        runner = self.runner
        if runner is not None:
            self.tasks.run("stop", runner.stop)

    def emergency_stop(self) -> None:
        runner, adapter = self.runner, self.adapter
        if runner is not None and runner.state not in TERMINAL_STATES:
            self.tasks.run("estop", runner.estop)
        elif adapter is not None:
            self.tasks.run("estop", adapter.estop)

    def clear_estop(self) -> None:
        if self.adapter is not None:
            self.tasks.run("clear_estop", self.adapter.clear_estop)

    def _scan_capabilities(self) -> None:
        """Requirement 5: right after loading a file, report which functions
        and axes it needs that this machine lacks — fully offline, before any
        Connect/Check."""
        profile, path = self.profile, self.gcode_path

        def _scan():
            result = interpret(parse_file(path), profile)
            return run_preflight(profile, adapter=None, result=result,
                                 offline=True)

        self.tasks.run("capability_scan", _scan)

    # ------------------------------------------------------- task completions
    def _on_task_finished(self, name: str, result: object,
                          error: Optional[str]) -> None:
        if error is not None:
            self._log(f"{name} failed: {error}")
            if name == "connect":
                self.conn_label.setText("connection failed")
            self._update_buttons()
            return

        if name == "capability_scan":
            findings = result.findings
            if findings:
                self.capability_view.setPlainText(
                    "\n".join(str(f) for f in findings))
            else:
                self.capability_view.setPlainText(
                    "no gaps: the machine covers everything this file needs")
        elif name == "apply_config":
            self._log(f"uploaded {result} — reset the board (M999 in DWC or "
                      f"power cycle) to apply, then re-home")
        elif name == "connect":
            self.adapter = result
            self.conn_label.setText("connected")
            self._log("controller connected")
            interval = 0.2 if self.profile.is_sim else 0.5
            self.poller = StatusPoller(self.adapter, interval_s=interval,
                                       parent=self)
            self.poller.status.connect(self._on_status)
            self.poller.failed.connect(
                lambda m: self._log(f"status poll failed: {m}"))
            self.poller.start()
        elif name == "disconnect":
            self.conn_label.setText("disconnected")
            self._log("controller disconnected")
            self.axis_table.setRowCount(0)
        elif name == "parse":
            self.usage_label.setText(result.summary())
            self._log(f"file loaded: {result.summary()}")
        elif name == "check":
            _, report = result
            for finding in report.findings:
                self._log(str(finding))
            passed = report.passed
            self.check_label.setText("PASS" if passed else "FAIL")
            self.check_label.setStyleSheet(
                f"color:{'#1a7f37' if passed else '#c0392b'};font-weight:bold;")
        elif name == "start":
            interp_result, report = result
            for finding in report.findings:
                self._log(str(finding))
            if not report.passed:
                self.check_label.setText("FAIL")
                self.check_label.setStyleSheet("color:#c0392b;font-weight:bold;")
                self._log("preflight FAIL — not starting")
            else:
                self._launch_runner(interp_result)
        self._update_buttons()

    def _launch_runner(self, interp_result) -> None:
        job_id = _dt.datetime.now().strftime("job-%Y%m%d-%H%M%S")
        report_path = self.report_dir / f"{job_id}.json"
        self.runner = PrintJobRunner(
            self.profile, self.adapter, interp_result, self.gcode_path,
            job_id=job_id, report_path=report_path,
            home_first=self.home_first_check.isChecked())
        self.runner.subscribe(self.bridge.dispatch)
        self.progress_bar.setValue(0)
        self._log(f"job {job_id} starting ({self.profile.mode_label} mode)")
        self.runner.start()

    # ------------------------------------------------------------ job events
    def _on_job_state(self, state: str) -> None:
        self.state_label.setText(state.upper())
        color = _STATE_COLORS.get(state, "#333333")
        self.state_label.setStyleSheet(f"color:{color};font-weight:bold;")
        self._update_buttons()

    def _on_progress(self, index: int, total: int, line_no: int,
                     percent: float) -> None:
        self.progress_bar.setValue(int(percent))
        self.line_label.setText(f"line {line_no} — action {index}/{total}")

    # ---------------------------------------------------------------- status
    def _on_status(self, snapshot: dict) -> None:
        axes = snapshot["axes"]
        self.axis_table.setRowCount(len(axes))
        for row, (name, ax) in enumerate(sorted(axes.items())):
            values = (name, f"{ax['position']:.3f}", f"{ax['speed']:.1f}",
                      "yes" if ax["homed"] else "no")
            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                if col:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight
                        | Qt.AlignmentFlag.AlignVCenter)
                self.axis_table.setItem(row, col, item)
        self._estop_active = bool(snapshot["estop"])
        if self._estop_active:
            self.estop_label.setText("EMERGENCY STOP ACTIVE")
            self.estop_label.setStyleSheet("color:#c0392b;font-weight:bold;")
        else:
            self.estop_label.setText("")

    # ------------------------------------------------------------------ misc
    def _job_state(self) -> Optional[PrintJobState]:
        return self.runner.state if self.runner is not None else None

    def _update_buttons(self) -> None:
        connected = self.adapter is not None
        state = self._job_state()
        job_active = state is not None and state not in TERMINAL_STATES
        self.load_profile_btn.setEnabled(not connected)
        self.connect_btn.setEnabled(self.profile is not None and not job_active)
        self.connect_btn.setText("Disconnect" if connected else "Connect")
        self.check_btn.setEnabled(connected and self.gcode_path is not None
                                  and not job_active)
        self.start_btn.setEnabled(connected and self.gcode_path is not None
                                  and not job_active)
        self.pause_btn.setEnabled(job_active and state is PrintJobState.RUNNING)
        self.resume_btn.setEnabled(job_active and state is PrintJobState.PAUSED)
        self.stop_btn.setEnabled(job_active)
        self.estop_btn.setEnabled(connected)
        self.clear_estop_btn.setEnabled(connected and not job_active)

    def _log(self, message: str) -> None:
        self._append_log_line(message)
        logger.debug("%s", message)

    def _append_log_line(self, line: str) -> None:
        self.log_view.appendPlainText(line)

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        if self.poller is not None:
            self.poller.stop()
        if self.runner is not None and self.runner.state not in TERMINAL_STATES:
            self.runner.stop()
            self.runner.join(timeout=5.0)
        if self.adapter is not None:
            try:
                self.adapter.disconnect()
            except Exception:  # pragma: no cover - best effort on close
                logger.exception("disconnect on close failed")
        self.log_handler.detach()
        super().closeEvent(event)

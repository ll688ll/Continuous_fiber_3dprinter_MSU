"""Manual control page (SRS FR-MAN-010..012): jog, go-to, per-axis homing.

Safety model, in layers:
* every motion goes through the adapter, so soft limits are enforced
  host-side (Duet: ``_check_soft_limits`` — RRF would silently clamp;
  FMC: the library refuses at enqueue) and unhomed moves are refused by
  the backend (RRF: "insufficient axes homed") — the refusal surfaces in
  the status strip and the log instead of moving;
* ONE manual operation in flight at a time — every motion button disables
  until the task returns (they are blocking calls run via ``TaskRunner``);
* everything disables while a print job is active, while disconnected,
  and while an e-stop is latched;
* the E-STOP mirror is always live when connected — it must never wait
  for a jog to finish.

Jog feed is clamped per axis to the profile's max at dispatch time.
"""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger("printer_msu.ui.manual")

JOG_STEPS_MM = (10.0, 1.0, 0.1)


class ManualPage(QWidget):
    """Owned by PrintConsoleWindow; reads window.adapter/tasks/runner."""

    def __init__(self, window) -> None:
        super().__init__()
        self._window = window
        self._rows: dict[str, dict] = {}   # axis name -> widgets
        self._busy = False
        self._connected = False
        self._job_active = False
        self._estop = False
        self._last_result: Optional[str] = None
        #: generation token: completions are matched to the dispatch that
        #: set _busy, so a STALE task finishing after a disconnect/reconnect
        #: cannot clear a newer operation's busy flag (review finding B)
        self._op_seq = 0
        #: tool-Z axes first — computed from the profile's tools like the
        #: print runner's _home_order (SRS D11). Bare adapter.home() is only
        #: tool-Z-first on the Duet (board homeall.g); the FMC adapter homes
        #: in ascending index order, so the order must be explicit here
        #: (review finding D).
        self._home_order: list[str] = []
        self._build_ui()

    @property
    def is_busy(self) -> bool:
        return self._busy

    # ------------------------------------------------------------------- ui
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        top = QHBoxLayout()
        top.addWidget(QLabel("Jog feed (mm/s):"))
        self.feed_spin = QDoubleSpinBox()
        self.feed_spin.setRange(0.1, 200.0)
        self.feed_spin.setValue(20.0)
        self.feed_spin.setDecimals(1)
        top.addWidget(self.feed_spin)
        self.home_all_btn = QPushButton("Home all")
        self.home_all_btn.clicked.connect(self._home_all)
        top.addWidget(self.home_all_btn)
        top.addStretch(1)
        self.estop_btn = QPushButton("E-STOP")
        self.estop_btn.setStyleSheet(
            "background-color:#c0392b;color:white;font-weight:bold;")
        self.estop_btn.clicked.connect(self._window.emergency_stop)
        top.addWidget(self.estop_btn)
        root.addLayout(top)

        self.axes_box = QGroupBox("Axes")
        self.axes_grid = QGridLayout(self.axes_box)
        root.addWidget(self.axes_box)

        self.status_label = QLabel("connect to the machine to enable "
                                   "manual control")
        root.addWidget(self.status_label)
        root.addStretch(1)

    def set_profile(self, profile) -> None:
        """(Re)build one row per logical axis from the profile."""
        while self.axes_grid.count():
            item = self.axes_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._rows.clear()
        self._home_order = []
        if profile is None:
            return
        tool_zs = [t.z_axis for t in profile.tools if t.z_axis]
        self._home_order = (tool_zs
                            + [a for a in profile.axis_names
                               if a not in tool_zs])
        headers = ("Axis", "Position", "Homed", "Jog", "Target (mm)", "", "")
        for col, text in enumerate(headers):
            label = QLabel(f"<b>{text}</b>")
            self.axes_grid.addWidget(label, 0, col)
        for r, name in enumerate(profile.axis_names, start=1):
            widgets: dict = {}
            self.axes_grid.addWidget(QLabel(name), r, 0)
            widgets["pos"] = QLabel("—")
            widgets["pos"].setAlignment(Qt.AlignmentFlag.AlignRight
                                        | Qt.AlignmentFlag.AlignVCenter)
            self.axes_grid.addWidget(widgets["pos"], r, 1)
            widgets["homed"] = QLabel("—")
            self.axes_grid.addWidget(widgets["homed"], r, 2)

            jog_row = QHBoxLayout()
            widgets["jog_buttons"] = []
            for step in JOG_STEPS_MM:
                minus = QPushButton(f"-{step:g}")
                minus.clicked.connect(
                    lambda _=False, a=name, s=step: self._jog(a, -s))
                jog_row.addWidget(minus)
                widgets["jog_buttons"].append(minus)
            for step in reversed(JOG_STEPS_MM):
                plus = QPushButton(f"+{step:g}")
                plus.clicked.connect(
                    lambda _=False, a=name, s=step: self._jog(a, +s))
                jog_row.addWidget(plus)
                widgets["jog_buttons"].append(plus)
            jog_holder = QWidget()
            jog_holder.setLayout(jog_row)
            self.axes_grid.addWidget(jog_holder, r, 3)

            widgets["target"] = QLineEdit()
            widgets["target"].setPlaceholderText("absolute mm")
            widgets["target"].setMaximumWidth(110)
            widgets["target"].returnPressed.connect(
                lambda a=name: self._go_to(a))
            self.axes_grid.addWidget(widgets["target"], r, 4)
            widgets["go"] = QPushButton("Go")
            widgets["go"].clicked.connect(lambda _=False, a=name: self._go_to(a))
            self.axes_grid.addWidget(widgets["go"], r, 5)
            widgets["home"] = QPushButton("Home")
            widgets["home"].clicked.connect(
                lambda _=False, a=name: self._home_axis(a))
            self.axes_grid.addWidget(widgets["home"], r, 6)
            self._rows[name] = widgets
        self.refresh_enabled()

    # ------------------------------------------------------------ state hooks
    def set_connected(self, connected: bool) -> None:
        self._connected = connected
        if not connected:
            # deliberately DO NOT clear _busy: the worker thread of an
            # in-flight op may outlive the disconnect by seconds; its
            # token-matched completion clears the flag when it truly ends
            self._last_result = None
            for widgets in self._rows.values():
                widgets["pos"].setText("—")
                widgets["homed"].setText("—")
        self.refresh_enabled()

    def update_status(self, snapshot: dict) -> None:
        self._estop = bool(snapshot.get("estop"))
        for name, ax in snapshot.get("axes", {}).items():
            widgets = self._rows.get(name)
            if widgets is None:
                continue
            widgets["pos"].setText(f"{ax['position']:.3f}")
            widgets["homed"].setText("yes" if ax["homed"] else "NO")
        self.refresh_enabled()

    def set_job_active(self, active: bool) -> None:
        self._job_active = active
        self.refresh_enabled()

    def refresh_enabled(self) -> None:
        allowed = (self._connected and not self._busy
                   and not self._job_active and not self._estop)
        for widgets in self._rows.values():
            for btn in widgets["jog_buttons"]:
                btn.setEnabled(allowed)
            widgets["go"].setEnabled(allowed)
            widgets["home"].setEnabled(allowed)
            widgets["target"].setEnabled(allowed)
        self.home_all_btn.setEnabled(allowed)
        # the E-STOP mirror never waits for a jog: live whenever connected
        self.estop_btn.setEnabled(self._connected)
        if not self._connected:
            self.status_label.setText(
                "connect to the machine to enable manual control")
        elif self._estop:
            self.status_label.setText(
                "e-stop active — clear it on the Print tab")
        elif self._job_active:
            self.status_label.setText(
                "a print job is running — manual control disabled")
        elif self._busy:
            pass  # the dispatching handler already wrote what is moving
        else:
            # keep the last action's outcome visible across poller refreshes
            self.status_label.setText(self._last_result or "ready")

    # ---------------------------------------------------------------- actions
    def _dispatch(self, description: str, fn) -> None:
        # refuse while a job preflight ('start'/'check') is already in
        # flight — job_active only turns true AFTER preflight passes, so
        # without this a jog could interleave with a launching print
        # (review finding A)
        if (self._busy or not self._connected or self._job_active
                or self._window.pending_job_tasks > 0):
            return
        self._busy = True
        self._op_seq += 1
        self.status_label.setText(description + "…")
        self.refresh_enabled()
        # manual busy also gates the Print tab (Start/Check/Connect/…)
        self._window.tasks.run(f"manual:{self._op_seq}", fn)
        self._window._update_buttons()

    def on_manual_finished(self, name: str, error: Optional[str]) -> None:
        try:
            seq = int(name.split(":", 1)[1])
        except (IndexError, ValueError):
            seq = self._op_seq
        if seq != self._op_seq:
            logger.debug("ignoring stale manual completion %s", name)
            return
        self._busy = False
        self._last_result = f"failed: {error}" if error else "done"
        self.status_label.setText(self._last_result)
        self.refresh_enabled()

    def _clamped_feed(self, axis: str) -> float:
        feed = float(self.feed_spin.value())
        adapter = self._window.adapter
        try:
            feed = min(feed, adapter.max_speeds()[axis])
        except Exception:  # unknown axis/backend quirk: let dispatch report it
            pass
        return feed

    def _jog(self, axis: str, delta_mm: float) -> None:
        adapter = self._window.adapter
        if adapter is None:
            return
        feed = self._clamped_feed(axis)
        self._dispatch(
            f"jog {axis} {delta_mm:+g} mm at {feed:g} mm/s",
            lambda: adapter.move_relative({axis: delta_mm}, feed=feed))

    def _go_to(self, axis: str) -> None:
        adapter = self._window.adapter
        if adapter is None:
            return
        raw = self._rows[axis]["target"].text().strip()
        try:
            target = float(raw)
        except ValueError:
            self.status_label.setText(f"{axis}: target {raw!r} is not a number")
            return
        feed = self._clamped_feed(axis)
        self._dispatch(
            f"move {axis} to {target:g} mm at {feed:g} mm/s",
            lambda: adapter.move_absolute({axis: target}, feed=feed))

    def _home_axis(self, axis: str) -> None:
        adapter = self._window.adapter
        if adapter is None:
            return
        self._dispatch(f"home {axis}", lambda: adapter.home([axis]))

    def _home_all(self) -> None:
        adapter = self._window.adapter
        if adapter is None:
            return
        # explicit tool-Z-first order (SRS D11) — correct on BOTH backends;
        # bare adapter.home() would home the FMC in ascending index order
        order = list(self._home_order) or None
        self._dispatch("home all", lambda: adapter.home(order))

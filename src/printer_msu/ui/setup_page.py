"""Machine setup page (V2-M6, requirement 3): edit the profile in tables,
preview/apply the generated board config, calibrate tool print planes.

Editing model: the page holds the profile's canonical v2 dict
(``profile.to_dict()``); table cells write back into that dict; Save
round-trips it through ``profile_from_dict`` (full validation — a bad cell is
rejected with the validator's message, nothing is written) and then
``profile_store.save_profile`` (atomic). Nested sections that have no table
columns (extruder/heater/fan/connection details) survive untouched.
"""

from __future__ import annotations

import copy
import json
import logging
from pathlib import Path
from typing import Any, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..config.profile import profile_from_dict
from ..config.profile_store import save_profile

logger = logging.getLogger("printer_msu.ui.setup")

_AXIS_COLUMNS = [
    ("name", "Axis"), ("controller", "Controller"), ("channel", "Channel"),
    ("letter", "Letter"), ("steps_per_mm", "Steps/mm"),
    ("max_speed_mm_s", "Max speed"), ("max_accel_mm_s2", "Max accel"),
    ("jerk_mm_s", "Jerk"), ("limit_min", "Min (mm)"), ("limit_max", "Max (mm)"),
    ("endstop_pin", "Endstop pin"), ("endstop_end", "End"),
    ("homes_up", "Homes up"),
]
_INT_FIELDS = {"channel"}
_FLOAT_FIELDS = {"steps_per_mm", "max_speed_mm_s", "max_accel_mm_s2",
                 "jerk_mm_s", "limit_min", "limit_max"}
_BOOL_FIELDS = {"homes_up"}


class SetupPage(QWidget):
    """Owned by PrintConsoleWindow; reads window.profile/adapter/tasks."""

    profile_saved = Signal(str)  # emits the saved path

    def __init__(self, window) -> None:
        super().__init__()
        self._window = window
        self._data: Optional[dict] = None
        self._path: Optional[Path] = None
        self._build_ui()

    # ------------------------------------------------------------------- ui
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        ctrl_box = QGroupBox("Controllers")
        cvl = QVBoxLayout(ctrl_box)
        self.controllers_table = QTableWidget(0, 3)
        self.controllers_table.setHorizontalHeaderLabels(
            ["Id", "Type", "Connection (JSON)"])
        self.controllers_table.verticalHeader().setVisible(False)
        cvl.addWidget(self.controllers_table)
        root.addWidget(ctrl_box)

        axes_box = QGroupBox("Axes")
        avl = QVBoxLayout(axes_box)
        self.axes_table = QTableWidget(0, len(_AXIS_COLUMNS))
        self.axes_table.setHorizontalHeaderLabels(
            [label for _, label in _AXIS_COLUMNS])
        self.axes_table.verticalHeader().setVisible(False)
        avl.addWidget(self.axes_table)
        root.addWidget(axes_box, 2)

        tools_box = QGroupBox("Tools")
        tvl = QVBoxLayout(tools_box)
        self.tools_table = QTableWidget(0, 4)
        self.tools_table.setHorizontalHeaderLabels(
            ["Tool", "Z axis", "Plane offset (mm)", "Hardware"])
        self.tools_table.verticalHeader().setVisible(False)
        tvl.addWidget(self.tools_table)
        trow = QHBoxLayout()
        self.calibrate_btn = QPushButton("Calibrate plane…")
        self.calibrate_btn.clicked.connect(self.calibrate_selected_tool)
        trow.addWidget(self.calibrate_btn)
        trow.addStretch(1)
        tvl.addLayout(trow)
        root.addWidget(tools_box, 1)

        brow = QHBoxLayout()
        self.save_btn = QPushButton("Save profile")
        self.save_btn.clicked.connect(self.save)
        self.preview_btn = QPushButton("Preview board config")
        self.preview_btn.clicked.connect(self._show_preview)
        self.apply_btn = QPushButton("Apply to board…")
        self.apply_btn.clicked.connect(self.apply_config)
        self.setup_status = QLabel("no profile loaded")
        brow.addWidget(self.save_btn)
        brow.addWidget(self.preview_btn)
        brow.addWidget(self.apply_btn)
        brow.addWidget(self.setup_status, 1)
        root.addLayout(brow)

    # -------------------------------------------------------------- populate
    def load_from(self, profile, path: Optional[str]) -> None:
        self._data = profile.to_dict()
        self._path = Path(path) if path else None
        self._populate()
        self.setup_status.setText(
            f"editing {profile.profile_name!r}"
            + (f" ({self._path.name})" if self._path else " (unsaved)"))

    def _populate(self) -> None:
        data = self._data or {}
        controllers = data.get("controllers", [])
        self.controllers_table.setRowCount(len(controllers))
        for row, c in enumerate(controllers):
            for col, value in enumerate(
                    (c["id"], c["type"], json.dumps(c["connection"]))):
                item = QTableWidgetItem(str(value))
                if col < 2:  # id/type are structural; edit the JSON only
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.controllers_table.setItem(row, col, item)

        axes = data.get("axes", [])
        self.axes_table.setRowCount(len(axes))
        for row, axis in enumerate(axes):
            for col, (key, _) in enumerate(_AXIS_COLUMNS):
                value = self._axis_cell(axis, key)
                self.axes_table.setItem(row, col,
                                        QTableWidgetItem("" if value is None
                                                         else str(value)))

        tools = data.get("tools", [])
        self.tools_table.setRowCount(len(tools))
        for row, tool in enumerate(tools):
            hardware = ", ".join(
                part for part in (
                    "extruder" if tool.get("extruder") else "",
                    "heater" if tool.get("heater") else "",
                    "fan" if tool.get("fan") else "") if part) or "-"
            cells = (str(tool["tool_index"]), str(tool.get("z_axis") or ""),
                     "" if tool.get("plane_offset_mm") is None
                     else str(tool["plane_offset_mm"]), hardware)
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col in (0, 3):  # index + hardware summary are not editable
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.tools_table.setItem(row, col, item)

    @staticmethod
    def _axis_cell(axis: dict, key: str) -> Any:
        if key == "limit_min":
            return (axis.get("limits_mm") or [None, None])[0]
        if key == "limit_max":
            return (axis.get("limits_mm") or [None, None])[1]
        if key == "endstop_pin":
            return (axis.get("endstop") or {}).get("pin")
        if key == "endstop_end":
            return (axis.get("endstop") or {}).get("end")
        return axis.get(key)

    # --------------------------------------------------------------- collect
    def collect(self) -> dict:
        """Tables -> canonical v2 dict (raises ValueError on bad cells)."""
        if self._data is None:
            raise ValueError("no profile loaded")
        data = copy.deepcopy(self._data)

        for row, c in enumerate(data.get("controllers", [])):
            raw = self._cell(self.controllers_table, row, 2)
            try:
                c["connection"] = json.loads(raw) if raw else {}
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"controller {c['id']}: connection is not valid JSON "
                    f"({exc})") from exc

        for row, axis in enumerate(data.get("axes", [])):
            limits = [None, None]
            endstop = dict(axis.get("endstop") or {})
            for col, (key, label) in enumerate(_AXIS_COLUMNS):
                raw = self._cell(self.axes_table, row, col)
                value = self._parse_cell(key, label, raw, axis.get("name"))
                if key == "limit_min":
                    limits[0] = value
                elif key == "limit_max":
                    limits[1] = value
                elif key == "endstop_pin":
                    endstop["pin"] = value or ""
                elif key == "endstop_end":
                    if value:
                        endstop["end"] = value
                elif value is None:
                    axis.pop(key, None)
                else:
                    axis[key] = value
            if limits[0] is not None and limits[1] is not None:
                axis["limits_mm"] = limits
            elif "limits_mm" in axis and limits == [None, None]:
                del axis["limits_mm"]
            if endstop.get("pin"):
                endstop.setdefault("end", "min")
                endstop.setdefault("inverted", True)
                axis["endstop"] = endstop
            else:
                axis.pop("endstop", None)

        for row, tool in enumerate(data.get("tools", [])):
            z_axis = self._cell(self.tools_table, row, 1)
            tool["z_axis"] = z_axis or None
            plane = self._cell(self.tools_table, row, 2)
            if plane:
                try:
                    tool["plane_offset_mm"] = float(plane)
                except ValueError as exc:
                    raise ValueError(
                        f"tool {tool['tool_index']}: plane offset "
                        f"{plane!r} is not a number") from exc
            else:
                tool.pop("plane_offset_mm", None)
        return data

    @staticmethod
    def _cell(table: QTableWidget, row: int, col: int) -> str:
        item = table.item(row, col)
        return item.text().strip() if item is not None else ""

    @staticmethod
    def _parse_cell(key: str, label: str, raw: str, axis_name) -> Any:
        if raw == "":
            return None
        try:
            if key in _INT_FIELDS:
                return int(raw)
            if key in _FLOAT_FIELDS:
                return float(raw)
            if key in _BOOL_FIELDS:
                return raw.lower() in ("true", "yes", "1", "x")
        except ValueError as exc:
            raise ValueError(f"axis {axis_name}: {label} {raw!r} is not "
                             f"numeric") from exc
        return raw

    # ------------------------------------------------------------------ save
    def save(self) -> bool:
        try:
            data = self.collect()
            profile = profile_from_dict(
                data, base_dir=self._path.parent if self._path else None)
        except Exception as exc:
            self.setup_status.setText(f"NOT saved: {exc}")
            QMessageBox.warning(self, "Profile invalid", str(exc))
            return False
        if self._path is None:
            QMessageBox.warning(self, "Profile", "no file path to save to")
            return False
        save_profile(self._path, profile)
        self._data = profile.to_dict()
        self.setup_status.setText(f"saved {self._path.name}")
        for warning in profile.validation_warnings:
            self._window._log(f"profile warning: {warning}")
        self.profile_saved.emit(str(self._path))
        return True

    # ---------------------------------------------------------- board config
    def preview_text(self) -> str:
        from ..config.duet_config_gen import generate_machine_g

        data = self.collect()
        profile = profile_from_dict(
            data, base_dir=self._path.parent if self._path else None)
        return generate_machine_g(profile)

    def _show_preview(self) -> None:
        try:
            text = self.preview_text()
        except Exception as exc:
            QMessageBox.warning(self, "Board config", str(exc))
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Generated /sys/msu_machine.g")
        layout = QVBoxLayout(dialog)
        view = QPlainTextEdit(text)
        view.setReadOnly(True)
        layout.addWidget(view)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.resize(560, 480)
        dialog.exec()

    def apply_config(self) -> None:
        """Upload the generated include; the board applies it on reset (D6)."""
        adapter = self._window.adapter
        client = getattr(adapter, "client", None)
        if client is None:
            QMessageBox.information(
                self, "Apply to board",
                "connect to a Duet controller first (the Setup page uploads "
                "over the same connection)")
            return
        try:
            text = self.preview_text()
        except Exception as exc:
            QMessageBox.warning(self, "Board config", str(exc))
            return
        if QMessageBox.question(
                self, "Apply to board",
                "Upload /sys/msu_machine.g to the board?\n\n"
                "Changes take effect after a reset (M999) — the board "
                "reboots and loses its homed state.") \
                != QMessageBox.StandardButton.Yes:
            return

        from ..config.duet_config_gen import BOARD_PATH

        def _upload():
            client.upload(BOARD_PATH, text.encode("ascii"))
            return BOARD_PATH

        self._window.tasks.run("apply_config", _upload)

    # ------------------------------------------------------------ calibration
    def calibrate_selected_tool(self) -> None:
        row = self.tools_table.currentRow()
        if row < 0 or self._data is None:
            QMessageBox.information(self, "Calibrate",
                                    "select a tool row first")
            return
        tool = self._data["tools"][row]
        z_axis = tool.get("z_axis")
        if not z_axis:
            QMessageBox.warning(self, "Calibrate",
                                f"tool {tool['tool_index']} has no z_axis")
            return
        adapter = self._window.adapter
        if adapter is None:
            QMessageBox.information(self, "Calibrate",
                                    "connect to the machine first")
            return
        dialog = CalibrateDialog(self, adapter, z_axis)
        if dialog.exec() == QDialog.DialogCode.Accepted \
                and dialog.captured_mm is not None:
            self.tools_table.item(row, 2).setText(f"{dialog.captured_mm:.3f}")
            self._window._log(
                f"tool {tool['tool_index']}: captured plane offset "
                f"{dialog.captured_mm:.3f} mm — press 'Save profile' to keep")


class CalibrateDialog(QDialog):
    """Jog the tool's Z down to touch the plane; Capture stores machine Z.

    Machine zero is at the TOP (SRS D11): jogging + moves DOWN toward the
    plane. Blocking controller calls run on the UI thread deliberately here —
    jog steps are <= 1 mm and the dialog is modal (nothing else to do).
    """

    STEPS = (1.0, 0.1, 0.01)
    FEED_MM_S = 5.0

    def __init__(self, parent, adapter, z_axis: str) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Calibrate print plane — {z_axis}")
        self._adapter = adapter
        self._z_axis = z_axis
        self.captured_mm: Optional[float] = None

        layout = QVBoxLayout(self)
        self.position_label = QLabel("position: ?")
        layout.addWidget(self.position_label)
        grid = QGridLayout()
        for col, step in enumerate(self.STEPS):
            down = QPushButton(f"+{step:g} (down)")
            down.clicked.connect(lambda _=False, s=step: self._jog(s))
            up = QPushButton(f"-{step:g} (up)")
            up.clicked.connect(lambda _=False, s=step: self._jog(-s))
            grid.addWidget(down, 0, col)
            grid.addWidget(up, 1, col)
        layout.addLayout(grid)
        buttons = QDialogButtonBox()
        capture = buttons.addButton("Capture",
                                    QDialogButtonBox.ButtonRole.AcceptRole)
        capture.clicked.connect(self._capture)
        cancel = buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        cancel.clicked.connect(self.reject)
        layout.addWidget(buttons)
        self._refresh()

    def _position(self) -> float:
        return self._adapter.full_status()["axes"][self._z_axis]["position"]

    def _refresh(self) -> None:
        try:
            self.position_label.setText(
                f"{self._z_axis} machine position: {self._position():.3f} mm")
        except Exception as exc:
            self.position_label.setText(f"position read failed: {exc}")

    def _jog(self, delta_mm: float) -> None:
        try:
            self._adapter.move_relative({self._z_axis: delta_mm},
                                        feed=self.FEED_MM_S)
        except Exception as exc:
            QMessageBox.warning(self, "Jog", str(exc))
        self._refresh()

    def _capture(self) -> None:
        try:
            self.captured_mm = self._position()
        except Exception as exc:
            QMessageBox.warning(self, "Capture", str(exc))
            return
        self.accept()

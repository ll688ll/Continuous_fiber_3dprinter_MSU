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
        arow = QHBoxLayout()
        self.add_axis_btn = QPushButton("Add axis")
        self.add_axis_btn.clicked.connect(self.add_axis)
        self.remove_axis_btn = QPushButton("Remove selected axis")
        self.remove_axis_btn.clicked.connect(self.remove_selected_axis)
        arow.addWidget(self.add_axis_btn)
        arow.addWidget(self.remove_axis_btn)
        arow.addStretch(1)
        avl.addLayout(arow)
        root.addWidget(axes_box, 2)

        tools_box = QGroupBox("Tools")
        tvl = QVBoxLayout(tools_box)
        self.tools_table = QTableWidget(0, 4)
        self.tools_table.setHorizontalHeaderLabels(
            ["Tool", "Z axis", "Plane offset (mm)", "Hardware"])
        self.tools_table.verticalHeader().setVisible(False)
        tvl.addWidget(self.tools_table)
        trow = QHBoxLayout()
        self.add_tool_btn = QPushButton("Add tool")
        self.add_tool_btn.clicked.connect(self.add_tool)
        self.remove_tool_btn = QPushButton("Remove selected tool")
        self.remove_tool_btn.clicked.connect(self.remove_selected_tool)
        self.hardware_btn = QPushButton("Hardware…")
        self.hardware_btn.clicked.connect(self.edit_selected_hardware)
        self.calibrate_btn = QPushButton("Calibrate plane…")
        self.calibrate_btn.clicked.connect(self.calibrate_selected_tool)
        trow.addWidget(self.add_tool_btn)
        trow.addWidget(self.remove_tool_btn)
        trow.addWidget(self.hardware_btn)
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

    # ------------------------------------------------------- structural edits
    def _sync_tables(self) -> bool:
        """Pull current cell edits into ``self._data`` before a structural
        change, so adding/removing rows never discards unsaved typing."""
        try:
            self._data = self.collect()
            return True
        except ValueError as exc:
            QMessageBox.warning(self, "Fix the highlighted value first",
                                str(exc))
            return False

    def _duet_controller_id(self) -> Optional[str]:
        for c in (self._data or {}).get("controllers", []):
            if c.get("type") == "duet":
                return c["id"]
        controllers = (self._data or {}).get("controllers", [])
        return controllers[0]["id"] if controllers else None

    def _free_channel(self, controller_id: str) -> int:
        used = {a.get("channel") for a in self._data.get("axes", [])
                if a.get("controller") == controller_id}
        used |= {t["extruder"].get("channel") for t in self._data.get("tools", [])
                 if t.get("extruder")
                 and t["extruder"].get("controller") == controller_id}
        for ch in range(6):
            if ch not in used:
                return ch
        return max((c for c in used if isinstance(c, int)), default=-1) + 1

    def _free_letter(self, controller_id: str) -> str:
        used = {a.get("letter") for a in self._data.get("axes", [])
                if a.get("controller") == controller_id}
        for letter in "UVWABC":  # extra-axis letters; XYZ stay for the gantry
            if letter not in used:
                return letter
        return "?"

    def add_axis(self) -> None:
        """Template for an additional tool-Z style axis (the common case).

        The template mirrors Z_T0's proven parameters; the user fixes name,
        channel, endstop pin (io6/io7/io8 are free — NEVER io0) and limits,
        then Save validates everything."""
        if self._data is None or not self._sync_tables():
            return
        cid = self._duet_controller_id()
        if cid is None:
            QMessageBox.warning(self, "Add axis", "no controller in profile")
            return
        existing = {a.get("name") for a in self._data["axes"]}
        k = 0
        while f"Z_T{k}" in existing:
            k += 1
        name = f"Z_T{k}"
        self._data["axes"].append({
            "name": name, "controller": cid,
            "channel": self._free_channel(cid),
            "letter": self._free_letter(cid),
            "steps_per_mm": 500.0, "max_speed_mm_s": 100.0,
            "max_accel_mm_s2": 800.0, "jerk_mm_s": 2.0,
            "limits_mm": [0.0, 282.0], "homes_up": True,
        })
        self._populate()
        self.setup_status.setText(
            f"added axis {name!r} — set its endstop pin (io6/io7/io8 are "
            f"free) and limits, then Save")

    def remove_selected_axis(self) -> None:
        row = self.axes_table.currentRow()
        if self._data is None or row < 0 or not self._sync_tables():
            return
        axis = self._data["axes"][row]
        users = [str(t["tool_index"]) for t in self._data.get("tools", [])
                 if t.get("z_axis") == axis.get("name")]
        warning = (f"\n\nWARNING: tool(s) T{', T'.join(users)} use it as "
                   f"their z_axis — Save will fail until they are updated."
                   if users else "")
        if QMessageBox.question(
                self, "Remove axis",
                f"Remove axis {axis.get('name')!r}?{warning}") \
                != QMessageBox.StandardButton.Yes:
            return
        del self._data["axes"][row]
        self._populate()

    def add_tool(self) -> None:
        if self._data is None or not self._sync_tables():
            return
        indices = [t["tool_index"] for t in self._data.get("tools", [])]
        self._data.setdefault("tools", []).append(
            {"tool_index": max(indices, default=-1) + 1, "z_axis": None})
        self._populate()
        self.setup_status.setText(
            "added tool — set its Z axis cell, then Save")

    def remove_selected_tool(self) -> None:
        row = self.tools_table.currentRow()
        if self._data is None or row < 0 or not self._sync_tables():
            return
        tool = self._data["tools"][row]
        if QMessageBox.question(
                self, "Remove tool",
                f"Remove tool T{tool['tool_index']}?") \
                != QMessageBox.StandardButton.Yes:
            return
        del self._data["tools"][row]
        self._populate()

    def edit_selected_hardware(self) -> None:
        """Edit the selected tool's extruder/heater/fan sections as JSON."""
        row = self.tools_table.currentRow()
        if self._data is None or row < 0:
            QMessageBox.information(self, "Hardware", "select a tool row first")
            return
        if not self._sync_tables():
            return
        tool = self._data["tools"][row]
        current = {k: tool[k] for k in ("extruder", "heater", "fan")
                   if tool.get(k)}
        if not current:
            cid = self._duet_controller_id() or "duet"
            current = {"extruder": {
                "controller": cid, "channel": self._free_channel(cid),
                "letter": "E", "steps_per_mm": 400.0,
                "max_speed_mm_s": 60.0, "max_accel_mm_s2": 500.0,
                "jerk_mm_s": 2.0}}
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Tool T{tool['tool_index']} hardware "
                              f"(extruder / heater / fan, JSON)")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(
            "Keys: extruder, heater, fan. Delete a key to remove that "
            "hardware. Heater stays out of the applied config until the "
            "safety gate clears (PRD v2)."))
        editor = QPlainTextEdit(json.dumps(current, indent=2))
        layout.addWidget(editor)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.resize(520, 360)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.apply_hardware_json(row, editor.toPlainText())
        except ValueError as exc:
            QMessageBox.warning(self, "Hardware", str(exc))

    def apply_hardware_json(self, row: int, text: str) -> None:
        """Parse + apply a hardware JSON edit to tool ``row`` (dialog-free
        entry point, also used by tests)."""
        try:
            parsed = json.loads(text) if text.strip() else {}
        except json.JSONDecodeError as exc:
            raise ValueError(f"not valid JSON: {exc}") from exc
        if not isinstance(parsed, dict) or \
                set(parsed) - {"extruder", "heater", "fan"}:
            raise ValueError("only 'extruder', 'heater' and 'fan' keys are "
                             "allowed here")
        tool = self._data["tools"][row]
        for key in ("extruder", "heater", "fan"):
            if parsed.get(key):
                tool[key] = parsed[key]
            else:
                tool.pop(key, None)
        self._populate()
        self.setup_status.setText(
            f"tool T{tool['tool_index']} hardware updated — Save to keep, "
            f"then Apply to board")

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

    def preview_files(self) -> dict[str, str]:
        """All generated board files: the include + homing macros."""
        from ..config.duet_config_gen import (BOARD_PATH,
                                              generate_homing_macros,
                                              generate_machine_g)

        data = self.collect()
        profile = profile_from_dict(
            data, base_dir=self._path.parent if self._path else None)
        files = {BOARD_PATH: generate_machine_g(profile)}
        for name, body in generate_homing_macros(profile).items():
            files[f"/sys/{name}"] = body
        return files

    def _show_preview(self) -> None:
        try:
            files = self.preview_files()
        except Exception as exc:
            QMessageBox.warning(self, "Board config", str(exc))
            return
        text = "\n".join(f"; ================= {path}\n{body}"
                         for path, body in files.items())
        dialog = QDialog(self)
        dialog.setWindowTitle("Generated board files (include + homing macros)")
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
            files = self.preview_files()
        except Exception as exc:
            QMessageBox.warning(self, "Board config", str(exc))
            return
        names = "\n".join(files)
        if QMessageBox.question(
                self, "Apply to board",
                f"Upload to the board:\n{names}\n\n"
                "Homing macros take effect immediately; the machine include "
                "needs a reset (M999) — the board reboots and loses its "
                "homed state.") \
                != QMessageBox.StandardButton.Yes:
            return

        def _upload():
            for path, body in files.items():
                client.upload(path, body.encode("ascii"))
            return ", ".join(files)

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

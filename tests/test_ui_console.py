"""Print console UI over the sim backend (printer PRD M4).

Runs Qt offscreen; the event loop is pumped manually via processEvents so no
extra test dependencies are needed. Skipped entirely when PySide6 is absent
(it is the [ui] extra).
"""

from __future__ import annotations

import copy
import json
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("fmc_motion")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from printer_msu.core.model.print_job import PrintJobState  # noqa: E402
from printer_msu.ui.print_console import PrintConsoleWindow  # noqa: E402

from printer_testlib import FMC_SIM_CONFIG, PROFILE_DATA  # noqa: E402

GCODE = """\
G21
G90
G28 X Y
G1 X10 Y10 F1500
G1 X30 Y10
G0 X5 Y5
M2
"""


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def pump(qapp, condition, timeout=30.0):
    """Process Qt events until ``condition()`` is true or timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    qapp.processEvents()
    return condition()


@pytest.fixture
def files(tmp_path):
    (tmp_path / "machine.json").write_text(
        json.dumps(copy.deepcopy(FMC_SIM_CONFIG)), encoding="utf-8")
    profile = copy.deepcopy(PROFILE_DATA)
    profile["motion"]["config"] = "machine.json"
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(profile), encoding="utf-8")
    gcode_path = tmp_path / "part.gcode"
    gcode_path.write_text(GCODE, encoding="utf-8")
    return profile_path, gcode_path


@pytest.fixture
def window(qapp, files, tmp_path):
    profile_path, gcode_path = files
    win = PrintConsoleWindow()
    win.report_dir = tmp_path / "logs"
    try:
        win.load_profile(str(profile_path))
        win.open_gcode(str(gcode_path))
        win.connect_machine()
        assert pump(qapp, lambda: win.adapter is not None), "connect timed out"
        yield win
    finally:
        win.close()
        qapp.processEvents()


def _job_done(win, state):
    return win.runner is not None and win.runner.state is state


def test_full_print_flow(qapp, window, tmp_path):
    assert window.conn_label.text() == "connected"
    assert pump(qapp, lambda: "motion segments" in window.usage_label.text())

    window.check_file()
    assert pump(qapp, lambda: window.check_label.text() == "PASS")

    window.start_print()
    assert pump(qapp, lambda: _job_done(window, PrintJobState.COMPLETED))
    assert pump(qapp, lambda: window.state_label.text() == "COMPLETED")
    assert window.progress_bar.value() == 100

    positions = window.adapter.get_status().positions
    assert positions["X"] == pytest.approx(5.0, abs=0.05)
    report_files = list((tmp_path / "logs").glob("job-*.json"))
    assert len(report_files) == 1
    report = json.loads(report_files[0].read_text(encoding="utf-8"))
    assert report["final_state"] == "completed"


def test_pause_resume_buttons(qapp, window, files):
    profile_path, gcode_path = files
    gcode_path.write_text(
        "G28 X Y\nG1 X150 F1500\nG1 Y100\nG1 X10\nM2\n", encoding="utf-8")
    window.open_gcode(str(gcode_path))
    window.start_print()
    assert pump(qapp, lambda: _job_done(window, PrintJobState.RUNNING))
    assert pump(qapp, lambda: window.pause_btn.isEnabled())

    window.pause_btn.click()
    assert pump(qapp, lambda: _job_done(window, PrintJobState.PAUSED))
    assert pump(qapp, lambda: window.resume_btn.isEnabled())

    window.resume_btn.click()
    assert pump(qapp, lambda: _job_done(window, PrintJobState.COMPLETED))


def test_estop_and_clear(qapp, window):
    window.start_print()
    assert pump(qapp, lambda: _job_done(window, PrintJobState.RUNNING))
    window.estop_btn.click()
    assert pump(qapp, lambda: _job_done(window, PrintJobState.STOPPED))
    assert pump(qapp,
                lambda: "EMERGENCY" in window.estop_label.text(), timeout=10.0)

    window.clear_estop_btn.click()
    assert pump(qapp, lambda: window.estop_label.text() == "", timeout=10.0)


def test_start_refused_on_preflight_failure(qapp, window, files):
    profile_path, gcode_path = files
    gcode_path.write_text("G1 X10 Y10 F600\nM2\n", encoding="utf-8")  # unhomed
    window.open_gcode(str(gcode_path))
    window.start_print()
    assert pump(qapp, lambda: window.check_label.text() == "FAIL")
    assert window.runner is None

    # the Home-first checkbox waives it
    window.home_first_check.setChecked(True)
    window.start_print()
    assert pump(qapp, lambda: _job_done(window, PrintJobState.COMPLETED))


def test_status_table_updates(qapp, window):
    assert pump(qapp, lambda: window.axis_table.rowCount() == 3)
    names = {window.axis_table.item(r, 0).text()
             for r in range(window.axis_table.rowCount())}
    assert names == {"X", "Y", "Z_T0"}


# ---------------------------------------------------------- V2-M6 additions
HEATED_GCODE = "T0\nG28\nM104 S200\nG1 X10 Y10 E1 F600\nM2\n"


def test_window_has_print_and_setup_tabs(qapp, window):
    labels = [window.tabs.tabText(i) for i in range(window.tabs.count())]
    assert labels == ["Print", "Manual", "Setup"]


def test_capability_panel_reports_gaps_offline(qapp, files, tmp_path):
    """Requirement 5 in the UI: gaps show right after opening the file,
    before any Connect."""
    profile_path, _ = files
    heated = tmp_path / "heated.gcode"
    heated.write_text(HEATED_GCODE, encoding="utf-8")
    win = PrintConsoleWindow()
    try:
        win.load_profile(str(profile_path))
        win.open_gcode(str(heated))
        assert pump(qapp,
                    lambda: "no heater" in win.capability_view.toPlainText())
        text = win.capability_view.toPlainText()
        assert "WITHOUT extrusion" in text
        assert win.adapter is None  # never connected
    finally:
        win.close()
        qapp.processEvents()


def test_setup_page_populates_and_saves_edit(qapp, files):
    profile_path, _ = files
    win = PrintConsoleWindow()
    try:
        win.load_profile(str(profile_path))
        page = win.setup_page
        # axes table shows the three migrated axes
        names = [page.axes_table.item(r, 0).text()
                 for r in range(page.axes_table.rowCount())]
        assert names == ["X", "Y", "Z_T0"]
        # edit the tool plane offset and save
        page.tools_table.item(0, 2).setText("123.456")
        assert page.save()
        saved = json.loads(profile_path.read_text(encoding="utf-8"))
        assert saved["schema_version"] == 2  # canonical v2 written
        assert saved["tools"][0]["plane_offset_mm"] == 123.456
        # window reloaded the saved profile via the signal
        assert pump(qapp,
                    lambda: win.profile.tool(0).plane_offset_mm == 123.456)
    finally:
        win.close()
        qapp.processEvents()


def test_setup_page_rejects_invalid_edit(qapp, files, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    # modal dialogs block forever offscreen; the status label carries the news
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **k: None))
    profile_path, _ = files
    win = PrintConsoleWindow()
    try:
        win.load_profile(str(profile_path))
        page = win.setup_page
        before = profile_path.read_text(encoding="utf-8")
        page.tools_table.item(0, 2).setText("not-a-number")
        assert not page.save()
        assert "NOT saved" in page.setup_status.text()
        assert profile_path.read_text(encoding="utf-8") == before
    finally:
        win.close()
        qapp.processEvents()


def test_setup_page_preview_generates_board_config(qapp, tmp_path):
    from printer_testlib import DUET_PROFILE_DATA

    duet_path = tmp_path / "duet.json"
    duet_path.write_text(json.dumps(DUET_PROFILE_DATA), encoding="utf-8")
    win = PrintConsoleWindow()
    try:
        win.load_profile(str(duet_path))
        text = win.setup_page.preview_text()
        assert text.splitlines()[0].startswith(";")
        assert "M584 X0 Y1 Z2" in text
        assert 'M574 X1 S1 P"!io3.in"' in text
    finally:
        win.close()
        qapp.processEvents()


def test_setup_page_add_axis_and_save(qapp, tmp_path):
    """Add-axis button: template row appears with a free channel/letter;
    filling name + endstop pin and saving produces a valid v2 profile."""
    from printer_testlib import DUET_PROFILE_DATA
    from printer_msu.config.profile import load_profile

    duet_path = tmp_path / "duet.json"
    duet_path.write_text(json.dumps(copy.deepcopy(DUET_PROFILE_DATA)),
                         encoding="utf-8")
    win = PrintConsoleWindow()
    try:
        win.load_profile(str(duet_path))
        page = win.setup_page
        page.add_axis()
        assert page.axes_table.rowCount() == 4
        row = 3
        from printer_msu.ui.setup_page import _AXIS_COLUMNS
        cols = {key: i for i, (key, _) in enumerate(_AXIS_COLUMNS)}
        assert page.axes_table.item(row, cols["channel"]).text() == "3"
        assert page.axes_table.item(row, cols["letter"]).text() == "U"
        page.axes_table.item(row, cols["endstop_pin"]).setText("io6.in")
        assert page.save()
        profile = load_profile(duet_path)
        axis = profile.axis("Z_T1")
        assert axis.channel == 3 and axis.letter == "U" and axis.homes_up
        assert axis.endstop.pin == "io6.in"
    finally:
        win.close()
        qapp.processEvents()


def test_setup_page_add_tool_and_extruder_hardware(qapp, tmp_path):
    from printer_testlib import DUET_PROFILE_DATA
    from printer_msu.config.profile import load_profile

    duet_path = tmp_path / "duet.json"
    duet_path.write_text(json.dumps(copy.deepcopy(DUET_PROFILE_DATA)),
                         encoding="utf-8")
    win = PrintConsoleWindow()
    try:
        win.load_profile(str(duet_path))
        page = win.setup_page
        # extruder on tool 0 via the dialog-free entry point
        page.apply_hardware_json(0, json.dumps({"extruder": {
            "controller": "duet", "channel": 4, "letter": "E",
            "steps_per_mm": 400.0, "max_speed_mm_s": 60.0,
            "max_accel_mm_s2": 500.0, "jerk_mm_s": 2.0}}))
        assert "extruder" in page.tools_table.item(0, 3).text()
        assert page.save()
        profile = load_profile(duet_path)
        assert profile.tools[0].extruder.channel == 4
        # bad key rejected
        try:
            page.apply_hardware_json(0, '{"laser": {}}')
            raise AssertionError("bad key accepted")
        except ValueError:
            pass
    finally:
        win.close()
        qapp.processEvents()


def test_setup_page_preview_includes_homing_macros(qapp, tmp_path):
    from printer_testlib import DUET_PROFILE_DATA

    duet_path = tmp_path / "duet.json"
    duet_path.write_text(json.dumps(copy.deepcopy(DUET_PROFILE_DATA)),
                         encoding="utf-8")
    win = PrintConsoleWindow()
    try:
        files = win_files = None
        win.load_profile(str(duet_path))
        files = win.setup_page.preview_files()
        assert "/sys/msu_machine.g" in files
        assert "/sys/homeall.g" in files and "/sys/homez.g" in files
        assert 'M98 P"homez.g"' in files["/sys/homeall.g"]
    finally:
        win.close()
        qapp.processEvents()


# ------------------------------------------------------- manual control tab
def _wait_manual_done(qapp, window):
    assert pump(qapp, lambda: not window.manual_page._busy)


def test_manual_tab_disabled_until_connected(qapp, files):
    """Interlock: everything off without a connection (fresh window)."""
    profile_path, _ = files
    win = PrintConsoleWindow()
    try:
        win.load_profile(str(profile_path))
        page = win.manual_page
        assert win.tabs.tabText(1) == "Manual"
        assert set(page._rows) == {"X", "Y", "Z_T0"}
        assert not page.home_all_btn.isEnabled()
        assert not page.estop_btn.isEnabled()
        for widgets in page._rows.values():
            assert not widgets["go"].isEnabled()
            assert all(not b.isEnabled() for b in widgets["jog_buttons"])
    finally:
        win.close()
        qapp.processEvents()


def test_manual_home_jog_and_goto(qapp, window):
    page = window.manual_page
    assert pump(qapp, lambda: page.home_all_btn.isEnabled())
    # home all first (sim axes start unhomed; jogs would be refused)
    page.home_all_btn.click()
    _wait_manual_done(qapp, window)
    assert "done" in page.status_label.text()
    home_pos = window.adapter.full_status()["axes"]["X"]["position"]
    x = page._rows["X"]
    x["jog_buttons"][5].click()  # +10 (rightmost = largest plus step)
    _wait_manual_done(qapp, window)
    assert window.adapter.full_status()["axes"]["X"]["position"] ==         pytest.approx(home_pos + 10.0, abs=0.05)
    x["jog_buttons"][0].click()  # -10
    _wait_manual_done(qapp, window)
    assert window.adapter.full_status()["axes"]["X"]["position"] ==         pytest.approx(home_pos, abs=0.05)
    # go-to absolute
    x["target"].setText("25")
    x["go"].click()
    _wait_manual_done(qapp, window)
    assert window.adapter.full_status()["axes"]["X"]["position"] ==         pytest.approx(25.0, abs=0.05)


def test_manual_goto_rejects_non_number(qapp, window):
    page = window.manual_page
    assert pump(qapp, lambda: page.home_all_btn.isEnabled())
    x = page._rows["X"]
    x["target"].setText("abc")
    x["go"].click()
    assert "not a number" in page.status_label.text()
    assert not page._busy


def test_manual_busy_blocks_second_dispatch(qapp, window):
    page = window.manual_page
    assert pump(qapp, lambda: page.home_all_btn.isEnabled())
    page.home_all_btn.click()          # long-ish op
    assert page._busy
    before = page.status_label.text()
    page._jog("X", 1.0)                # must be ignored while busy
    assert page.status_label.text() == before
    _wait_manual_done(qapp, window)


# --------------------------- review findings (workflow 2026-09-01) pinned
def test_manual_busy_gates_print_tab_actions(qapp, window):
    """Finding A: while a manual op is in flight, Start/Check/Connect/
    Clear-E-Stop must be disabled; the E-STOP mirror stays live."""
    page = window.manual_page
    assert pump(qapp, lambda: page.home_all_btn.isEnabled())
    page.home_all_btn.click()
    assert page._busy
    assert not window.start_btn.isEnabled()
    assert not window.check_btn.isEnabled()
    assert not window.connect_btn.isEnabled()
    assert not window.clear_estop_btn.isEnabled()
    assert window.estop_btn.isEnabled()
    assert page.estop_btn.isEnabled()
    _wait_manual_done(qapp, window)
    assert window.start_btn.isEnabled()


def test_manual_dispatch_refused_while_preflight_pending(qapp, window):
    """Finding A (reverse): job_active only turns true after preflight, so
    the pending-task counter must close the gap."""
    page = window.manual_page
    assert pump(qapp, lambda: page.home_all_btn.isEnabled())
    window._pending_job_tasks = 1
    try:
        page._jog("X", 1.0)
        assert not page._busy  # refused
    finally:
        window._pending_job_tasks = 0


def test_stale_manual_completion_ignored(qapp, window):
    """Finding B: a completion from an older dispatch must not clear a
    newer operation's busy flag."""
    page = window.manual_page
    assert pump(qapp, lambda: page.home_all_btn.isEnabled())
    page._busy = True
    page._op_seq = 5
    page.on_manual_finished("manual:4", None)   # stale
    assert page._busy
    page.on_manual_finished("manual:5", None)   # current
    assert not page._busy


def test_manual_home_all_orders_tool_z_first_on_fmc(qapp, window):
    """Finding D: bare adapter.home() homes the FMC in ascending index
    order (X,Y,Z) — the manual page must pass the explicit tool-Z-first
    order (SRS D11)."""
    page = window.manual_page
    assert page._home_order[0] == "Z_T0"
    assert pump(qapp, lambda: page.home_all_btn.isEnabled())
    page.home_all_btn.click()
    _wait_manual_done(qapp, window)
    homes = [h["axis"] for h in window.adapter._ctrl.history
             if h["op"] == "home"]
    assert homes[0] == 2  # Z_T0 is controller axis 2; it must home first


def test_disconnect_failure_still_disables_manual_page(qapp, window):
    """Finding C: a failed disconnect leaves adapter=None — the manual
    page must not keep a live-looking (but dead) E-STOP mirror."""
    page = window.manual_page
    assert pump(qapp, lambda: page.home_all_btn.isEnabled())

    def boom():
        raise RuntimeError("link died")

    window.adapter.disconnect = boom
    window.disconnect_machine()
    assert pump(qapp, lambda: not page._connected)
    assert not page.estop_btn.isEnabled()

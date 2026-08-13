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

"""CLI behavior + exit codes (FR-CLI-01..02), in-process for speed."""

from __future__ import annotations

import copy
import json

import pytest

pytest.importorskip("fmc_motion")

from printer_msu.cli import main

from printer_testlib import FMC_SIM_CONFIG, PROFILE_DATA

GOOD_GCODE = """\
G21
G90
G28 X Y
G1 X10 Y10 F600
G0 X5 Y5
M2
"""


@pytest.fixture
def setup(tmp_path):
    machine = copy.deepcopy(FMC_SIM_CONFIG)
    (tmp_path / "machine.json").write_text(json.dumps(machine), encoding="utf-8")
    profile = copy.deepcopy(PROFILE_DATA)
    profile["motion"]["config"] = "machine.json"
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(profile), encoding="utf-8")
    gcode = tmp_path / "part.gcode"
    gcode.write_text(GOOD_GCODE, encoding="utf-8")
    return tmp_path, profile_path, gcode


def test_check_pass(setup, capsys):
    tmp_path, profile_path, gcode = setup
    rc = main(["check", str(gcode), "--profile", str(profile_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "preflight: PASS" in out


def test_check_fails_on_arcs(setup, capsys):
    tmp_path, profile_path, gcode = setup
    gcode.write_text("G28 X Y\nG2 X10 Y10 I5 J0 F600\nM2\n", encoding="utf-8")
    rc = main(["check", str(gcode), "--profile", str(profile_path)])
    out = capsys.readouterr().out
    assert rc == 2
    assert "preflight: FAIL" in out
    assert "arcs" in out


def test_check_reports_skipped_tool_commands(setup, capsys):
    tmp_path, profile_path, gcode = setup
    gcode.write_text("G28 X Y\nM104 S200\nG1 X10 E1 F600\nM2\n",
                     encoding="utf-8")
    rc = main(["check", str(gcode), "--profile", str(profile_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "M104 x1" in out


def test_print_completes_and_writes_report(setup, capsys):
    tmp_path, profile_path, gcode = setup
    report = tmp_path / "job.json"
    rc = main(["print", str(gcode), "--profile", str(profile_path),
               "--report", str(report)])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "COMPLETED" in out
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["final_state"] == "completed"
    assert data["gcode_sha256"]
    assert data["profile"]["profile_name"] == PROFILE_DATA["profile_name"]


def test_print_refuses_on_preflight_failure(setup, capsys):
    tmp_path, profile_path, gcode = setup
    gcode.write_text("G1 X10 Y10 F600\nM2\n", encoding="utf-8")  # unhomed
    rc = main(["print", str(gcode), "--profile", str(profile_path),
               "--report", str(tmp_path / "job.json")])
    assert rc == 2
    assert "not starting" in capsys.readouterr().out


def test_print_home_first_fixes_unhomed(setup, capsys):
    tmp_path, profile_path, gcode = setup
    gcode.write_text("G1 X10 Y10 F600\nM2\n", encoding="utf-8")
    rc = main(["print", str(gcode), "--profile", str(profile_path),
               "--home-first", "--report", str(tmp_path / "job.json")])
    assert rc == 0, capsys.readouterr().out


def test_missing_profile_is_usage_error(setup, capsys):
    tmp_path, profile_path, gcode = setup
    rc = main(["check", str(gcode), "--profile", str(tmp_path / "nope.json")])
    assert rc == 3

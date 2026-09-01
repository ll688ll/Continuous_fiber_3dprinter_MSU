from __future__ import annotations

import copy
import json

import pytest

from printer_msu.config.profile import load_profile, profile_from_dict
from printer_msu.errors import ProfileError

from printer_testlib import PROFILE_DATA, make_profile


def _data(**overrides):
    data = copy.deepcopy(PROFILE_DATA)
    data.update(overrides)
    return data


# ------------------------------------------------------- v1 load + migration
def test_valid_v1_profile_loads():
    profile = make_profile()
    assert profile.profile_name == "test sim printer"
    # legacy view survives migration for the FMC path
    assert profile.motion.axis_map["Z_T0"] == 2
    assert profile.tool(0).z_axis == "Z_T0"
    assert profile.tool(1) is None
    assert profile.policies.require_homed is True
    # migrated structure
    assert [c.type for c in profile.controllers] == ["fmc_motion"]
    assert profile.axis_names == ("X", "Y", "Z_T0")
    assert profile.axis("Z_T0").channel == 2
    assert profile.mode_label == "sim"
    assert profile.is_sim


def test_v1_z_bed_offset_migrates_to_tool_plane_offset():
    profile = make_profile(workspace={"z_mode": "bed", "z_bed_offset_mm": 250.0})
    assert profile.tool(0).plane_offset_mm == 250.0
    assert profile.workspace.z_mode == "bed"


def test_missing_required_key():
    data = _data()
    del data["motion"]
    with pytest.raises(ProfileError, match="missing required key"):
        profile_from_dict(data)


def test_duplicate_axis_indices_rejected():
    data = _data()
    data["motion"] = dict(data["motion"], axis_map={"X": 0, "Y": 0})
    with pytest.raises(ProfileError, match="already assigned"):
        profile_from_dict(data)


def test_unknown_z_axis_rejected():
    data = _data(tools=[{"tool_index": 0, "z_axis": "Z_T9"}])
    with pytest.raises(ProfileError, match="not a configured axis"):
        profile_from_dict(data)


def test_duplicate_tool_rejected():
    data = _data(tools=[{"tool_index": 0}, {"tool_index": 0}])
    with pytest.raises(ProfileError, match="duplicate tool_index"):
        profile_from_dict(data)


def test_bad_mode_rejected():
    data = _data()
    data["motion"] = dict(data["motion"], mode="pretend")
    with pytest.raises(ProfileError, match="mode"):
        profile_from_dict(data)


def test_bad_policy_value_rejected():
    with pytest.raises(ProfileError, match="unknown_command"):
        make_profile(policies={"unknown_command": "explode"})


def test_default_tool_must_exist():
    with pytest.raises(ProfileError, match="default_tool"):
        make_profile(gcode={"default_tool": 7})


def test_workspace_validation():
    profile = make_profile(workspace={"xy_offset": {"X": 30.0},
                                      "z_mode": "bed", "z_bed_offset_mm": 250.0})
    assert profile.tool(0).plane_offset_mm == 250.0
    # bed mode without any plane offset (v1: z_bed_offset_mm) is rejected
    with pytest.raises(ProfileError, match="z_bed_offset_mm"):
        make_profile(workspace={"z_mode": "bed"})
    with pytest.raises(ProfileError, match="only X/Y"):
        make_profile(workspace={"xy_offset": {"Z_T0": 5.0}})
    with pytest.raises(ProfileError, match="z_mode"):
        make_profile(workspace={"z_mode": "upside-down"})


def test_load_profile_resolves_config_relative_to_file(tmp_path):
    (tmp_path / "machine.json").write_text("{}", encoding="utf-8")
    data = _data()
    data["motion"] = dict(data["motion"], config="machine.json")
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(data), encoding="utf-8")
    profile = load_profile(profile_path)
    assert profile.motion.config_path == (tmp_path / "machine.json").resolve()


def test_load_profile_missing_file():
    with pytest.raises(ProfileError, match="not found"):
        load_profile("no/such/profile.json")


# ------------------------------------------------------------- v2 structure
def _duet_axis(name="X", channel=0, letter="X", **overrides):
    axis = {
        "name": name, "controller": "duet", "channel": channel, "letter": letter,
        "steps_per_mm": 142.857, "max_speed_mm_s": 200.0,
        "max_accel_mm_s2": 1000.0, "jerk_mm_s": 8.0,
        "limits_mm": [0.0, 987.448],
        "endstop": {"end": "min", "pin": "io3.in", "inverted": True},
    }
    axis.update(overrides)
    return axis


def _v2_data(**overrides):
    data = {
        "schema_version": 2,
        "profile_name": "duet test",
        "controllers": [
            {"id": "duet", "type": "duet",
             "connection": {"host": "192.168.0.40"}},
        ],
        "axes": [
            _duet_axis("X", 0, "X"),
            _duet_axis("Y", 1, "Y",
                       endstop={"end": "min", "pin": "io4.in", "inverted": True},
                       limits_mm=[0.0, 290.269]),
            _duet_axis("Z_T0", 2, "Z", homes_up=True,
                       steps_per_mm=500.0, max_speed_mm_s=100.0,
                       max_accel_mm_s2=800.0, jerk_mm_s=2.0,
                       endstop={"end": "min", "pin": "io5.in", "inverted": True},
                       limits_mm=[0.0, 282.306]),
        ],
        "tools": [{"tool_index": 0, "z_axis": "Z_T0", "plane_offset_mm": 100.0}],
        "workspace": {"xy_offset": {"X": 30.0, "Y": 30.0}, "z_mode": "bed"},
    }
    data.update(overrides)
    return data


def test_v2_duet_profile_loads():
    profile = profile_from_dict(_v2_data())
    assert profile.motion is None  # no legacy view on duet profiles
    assert profile.mode_label == "duet"
    assert not profile.is_sim
    assert profile.motion_controller.duet_host == "192.168.0.40"
    assert profile.axis("Z_T0").homes_up
    assert profile.axis("Z_T0").letter == "Z"
    assert profile.controller("duet").connection["http_timeout_s"] == 20.0


def test_v2_duet_axis_requires_full_spec():
    data = _v2_data()
    del data["axes"][0]["steps_per_mm"]
    with pytest.raises(ProfileError, match="steps_per_mm is required"):
        profile_from_dict(data)
    data = _v2_data()
    del data["axes"][0]["limits_mm"]
    with pytest.raises(ProfileError, match="limits_mm is required"):
        profile_from_dict(data)
    data = _v2_data()
    del data["axes"][0]["letter"]
    with pytest.raises(ProfileError, match="single-letter"):
        profile_from_dict(data)


def test_v2_io0_endstop_forbidden():
    # io0.in is physically serial0.rx on the MB6XD (hardware trap 2026-08-25)
    data = _v2_data()
    data["axes"][0]["endstop"]["pin"] = "io0.in"
    with pytest.raises(ProfileError, match="io0.in"):
        profile_from_dict(data)
    data["axes"][0]["endstop"]["pin"] = "!io0.in"
    with pytest.raises(ProfileError, match="io0.in"):
        profile_from_dict(data)


def test_v2_duplicate_channel_and_letter_rejected():
    data = _v2_data()
    data["axes"][1]["channel"] = 0
    with pytest.raises(ProfileError, match="already assigned"):
        profile_from_dict(data)
    data = _v2_data()
    data["axes"][1]["letter"] = "X"
    with pytest.raises(ProfileError, match="letter 'X'"):
        profile_from_dict(data)


def test_v2_axes_on_two_controllers_refused_this_phase():
    data = _v2_data()
    data["controllers"].append(
        {"id": "fmc", "type": "fmc_motion",
         "connection": {"config": "machine.json", "mode": "sim"}})
    data["axes"].append({"name": "Z_T1", "controller": "fmc", "channel": 0})
    with pytest.raises(ProfileError, match="span controllers"):
        profile_from_dict(data)


def test_v2_bed_mode_requires_plane_offset():
    data = _v2_data()
    del data["tools"][0]["plane_offset_mm"]
    with pytest.raises(ProfileError, match="plane_offset_mm"):
        profile_from_dict(data)


def test_v2_extruder_channel_collision_rejected():
    data = _v2_data()
    data["tools"][0]["extruder"] = {
        "controller": "duet", "channel": 2,  # collides with Z_T0's driver
        "steps_per_mm": 400.0, "max_speed_mm_s": 60.0, "max_accel_mm_s2": 500.0}
    with pytest.raises(ProfileError, match="already assigned"):
        profile_from_dict(data)


def test_v2_cross_controller_extruder_warns():
    data = _v2_data()
    data["controllers"].append(
        {"id": "fmc", "type": "fmc_motion",
         "connection": {"config": "machine.json", "mode": "sim"}})
    data["tools"][0]["extruder"] = {
        "controller": "fmc", "channel": 0,
        "steps_per_mm": 400.0, "max_speed_mm_s": 60.0, "max_accel_mm_s2": 500.0}
    profile = profile_from_dict(data)
    assert any("planner-synced" in w for w in profile.validation_warnings)


def test_to_dict_emits_canonical_v2_and_round_trips():
    # v1 in -> canonical v2 out -> reload -> identical snapshot (golden)
    profile = make_profile(workspace={"z_mode": "bed", "z_bed_offset_mm": 250.0})
    snapshot = profile.to_dict()
    assert snapshot["schema_version"] == 2
    assert "motion" not in snapshot
    assert snapshot["axes"][0] == {"name": "X", "controller": "fmc", "channel": 0}
    assert snapshot["tools"][0]["plane_offset_mm"] == 250.0
    assert "z_bed_offset_mm" not in snapshot["workspace"]
    reloaded = profile_from_dict(snapshot)
    assert reloaded.to_dict() == snapshot

    duet = profile_from_dict(_v2_data())
    duet_snapshot = duet.to_dict()
    reloaded = profile_from_dict(duet_snapshot)
    assert reloaded.to_dict() == duet_snapshot


def test_v2_explicit_z_bed_offset_rejected():
    data = _v2_data()
    data["workspace"]["z_bed_offset_mm"] = 100.0
    with pytest.raises(ProfileError, match="replaced by per-tool"):
        profile_from_dict(data)


def test_shipped_v2_configs_load():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "configs"
    duet = load_profile(root / "duet_lab.json")
    assert duet.mode_label == "duet"
    assert duet.axis("Z_T0").steps_per_mm == 500.0
    sim_v2 = load_profile(root / "printer_sim_v2.json")
    assert sim_v2.is_sim
    assert sim_v2.motion is not None  # fmc path keeps the legacy view


def test_shipped_v1_config_still_loads():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "configs"
    lab = load_profile(root / "printer_msu_lab.json")
    assert lab.motion is not None and lab.motion.mode == "real"
    assert lab.tool(0).plane_offset_mm is not None  # migrated from workspace


def test_profile_store_atomic_save_round_trip(tmp_path):
    from printer_msu.config.profile_store import save_profile

    profile = profile_from_dict(_v2_data())
    target = tmp_path / "saved.json"
    save_profile(target, profile)
    text = target.read_text(encoding="utf-8")
    assert "\r\n" not in text  # LF only, on every platform
    reloaded = load_profile(target)
    assert reloaded.to_dict() == profile.to_dict()
    # no temp litter
    assert [p.name for p in tmp_path.iterdir()] == ["saved.json"]

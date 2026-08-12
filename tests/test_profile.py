from __future__ import annotations

import copy

import pytest

from printer_msu.config.profile import load_profile, profile_from_dict
from printer_msu.errors import ProfileError

from printer_testlib import PROFILE_DATA, make_profile


def _data(**overrides):
    data = copy.deepcopy(PROFILE_DATA)
    data.update(overrides)
    return data


def test_valid_profile_loads():
    profile = make_profile()
    assert profile.profile_name == "test sim printer"
    assert profile.motion.axis_map["Z_T0"] == 2
    assert profile.tool(0).z_axis == "Z_T0"
    assert profile.tool(1) is None
    assert profile.policies.require_homed is True


def test_missing_required_key():
    data = _data()
    del data["motion"]
    with pytest.raises(ProfileError, match="missing required key"):
        profile_from_dict(data)


def test_duplicate_axis_indices_rejected():
    data = _data()
    data["motion"] = dict(data["motion"], axis_map={"X": 0, "Y": 0})
    with pytest.raises(ProfileError, match="duplicate controller indices"):
        profile_from_dict(data)


def test_unknown_z_axis_rejected():
    data = _data(tools=[{"tool_index": 0, "z_axis": "Z_T9"}])
    with pytest.raises(ProfileError, match="not in motion.axis_map"):
        profile_from_dict(data)


def test_duplicate_tool_rejected():
    data = _data(tools=[{"tool_index": 0}, {"tool_index": 0}])
    with pytest.raises(ProfileError, match="duplicate tool_index"):
        profile_from_dict(data)


def test_bad_mode_rejected():
    data = _data()
    data["motion"] = dict(data["motion"], mode="pretend")
    with pytest.raises(ProfileError, match="motion.mode"):
        profile_from_dict(data)


def test_bad_policy_value_rejected():
    with pytest.raises(ProfileError, match="unknown_command"):
        make_profile(policies={"unknown_command": "explode"})


def test_default_tool_must_exist():
    with pytest.raises(ProfileError, match="default_tool"):
        make_profile(gcode={"default_tool": 7})


def test_load_profile_resolves_config_relative_to_file(tmp_path):
    import json

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

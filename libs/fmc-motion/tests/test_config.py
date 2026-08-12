from __future__ import annotations

import json

import pytest

from fmc_motion import ConfigError, ControllerConfig, create_controller

from fmc_testlib import base_config_dict


def _dict(**overrides):
    return base_config_dict(**overrides)


def test_round_trip_json(tmp_path):
    cfg = ControllerConfig.from_dict(_dict())
    path = tmp_path / "machine.json"
    cfg.save(path)
    loaded = ControllerConfig.load(path)
    assert loaded.axis(0).name == "X"
    assert loaded.axis(0).defaults.speed == 100.0
    assert loaded.sim.time_scale == 50.0
    assert loaded.source_path == str(path)


def test_duplicate_axis_index_rejected():
    data = _dict()
    data["axes"][1]["index"] = 0
    with pytest.raises(ConfigError, match="duplicate axis index"):
        ControllerConfig.from_dict(data)


def test_bad_homing_direction_rejected():
    data = _dict()
    data["axes"][0]["homing"]["direction"] = "sideways"
    with pytest.raises(ConfigError, match="homing.direction"):
        ControllerConfig.from_dict(data)


def test_negative_speed_rejected():
    data = _dict()
    data["axes"][0]["defaults"]["speed"] = -5
    with pytest.raises(ConfigError, match="speed"):
        ControllerConfig.from_dict(data)


def test_defaults_exceeding_max_rejected():
    data = _dict()
    data["axes"][0]["defaults"]["speed"] = 9999
    with pytest.raises(ConfigError, match="exceeds max"):
        ControllerConfig.from_dict(data)


def test_axis_index_out_of_range_rejected():
    data = _dict()
    data["axes"][0]["index"] = 6
    with pytest.raises(ConfigError, match="0..5"):
        ControllerConfig.from_dict(data)


def test_unknown_mode_rejected():
    with pytest.raises(ConfigError, match="unknown mode"):
        create_controller(_dict(), mode="pretend")


def test_default_axis_names_assigned():
    data = _dict()
    data["axes"][0].pop("name")
    cfg = ControllerConfig.from_dict(data)
    assert cfg.axis(0).name == "X"


def test_save_config_persists_discovered_limits(tmp_path):
    ctrl = create_controller(_dict(), mode="sim")
    ctrl.connect()
    try:
        report = ctrl.self_check(axes=[0])
        assert report.passed
        path = tmp_path / "machine.json"
        ctrl.save_config(str(path))
    finally:
        ctrl.disconnect()
    data = json.loads(path.read_text(encoding="utf-8"))
    saved = [a for a in data["axes"] if a["index"] == 0][0]
    assert saved["soft_limits"]["min"] is not None
    assert saved["soft_limits"]["max"] is not None
    assert saved["soft_limits"]["discovered_at"]
    # reload keeps the working range
    cfg = ControllerConfig.load(path)
    assert cfg.axis(0).soft_limits.known

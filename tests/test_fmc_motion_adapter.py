"""FmcMotionAdapter: printer_msu HAL contract on top of fmc_motion (sim)."""

from __future__ import annotations

import time

import pytest

fmc_motion = pytest.importorskip("fmc_motion")

from printer_msu.controllers.motion.base_motion_controller import StopMode  # noqa: E402
from printer_msu.controllers.motion.fmc_motion_adapter import FmcMotionAdapter  # noqa: E402

CONFIG = {
    "connection": {"device_id": 1, "ip": "192.168.0.30", "port": 8088},
    "poll_interval_s": 0.002,
    "axes": [
        {"index": 0, "name": "X",
         "homing": {"direction": "negative", "speed": 40, "fall_step": 2},
         "defaults": {"speed": 100, "accel": 800, "decel": 800}},
        {"index": 1, "name": "Y",
         "homing": {"direction": "negative", "speed": 40, "fall_step": 2},
         "defaults": {"speed": 100, "accel": 800, "decel": 800}},
    ],
    "sim": {"time_scale": 50.0,
            "axes": {"0": {"travel_mm": 200}, "1": {"travel_mm": 150}}},
}


@pytest.fixture
def adapter():
    ctrl = fmc_motion.create_controller(CONFIG, mode="sim")
    adapter = FmcMotionAdapter(ctrl)
    adapter.connect()
    yield adapter
    adapter.disconnect()


def test_home_and_move_by_name(adapter):
    adapter.home(["X", "Y"])
    adapter.move_absolute({"X": 30.0, "Y": 20.0}, feed=100.0)
    status = adapter.get_status()
    assert status.positions["X"] == pytest.approx(30.0, abs=0.02)
    assert status.positions["Y"] == pytest.approx(20.0, abs=0.02)
    assert not status.emergency_active
    # busy settles once the axis workers finish bookkeeping (within ms of a
    # blocking call returning); poll briefly rather than assert instantly
    deadline = time.monotonic() + 2.0
    while adapter.get_status().run_state != "idle":
        assert time.monotonic() < deadline, "run_state never settled to idle"
        time.sleep(0.005)


def test_move_relative_and_stop(adapter):
    adapter.home(["X"])
    adapter.move_relative({"X": 10.0}, feed=100.0)
    assert adapter.get_status().positions["X"] == pytest.approx(12.0, abs=0.05)
    adapter.stop(StopMode.IMMEDIATE)  # no motion in flight: no-op, no error


def test_estop_reflected_in_status(adapter):
    adapter.estop()
    assert adapter.get_status().emergency_active


def test_unknown_axis_rejected(adapter):
    with pytest.raises(KeyError, match="unknown axis"):
        adapter.move_absolute({"Q": 5.0}, feed=50.0)


def test_capabilities_include_names(adapter):
    caps = adapter.capabilities()
    assert caps["axis_names"] == ["X", "Y"]
    assert caps["sim"] is True
    assert caps["interpolation"] is True


def test_two_axis_absolute_move_uses_line_interpolation(adapter):
    adapter.home(["X", "Y"])
    adapter.move_absolute({"X": 40.0, "Y": 25.0}, feed=100.0)
    line_ops = [h for h in adapter._ctrl.history if h["op"] == "line"]
    assert line_ops, "expected move_line for a 2-axis absolute move"
    assert line_ops[-1]["targets"] == {0: 40.0, 1: 25.0}
    assert line_ops[-1]["speed"] == 100.0


def test_single_axis_and_relative_moves_stay_independent(adapter):
    adapter.home(["X", "Y"])
    adapter.move_absolute({"X": 15.0}, feed=100.0)
    adapter.move_relative({"X": 5.0, "Y": 5.0}, feed=100.0)
    ops = [h["op"] for h in adapter._ctrl.history]
    assert "line" not in ops
    assert adapter.get_status().positions["X"] == pytest.approx(20.0, abs=0.05)


def test_homed_axes_and_max_speeds(adapter):
    assert adapter.homed_axes() == set()
    adapter.home(["X"])
    assert adapter.homed_axes() == {"X"}
    speeds = adapter.max_speeds()
    assert set(speeds) == {"X", "Y"}
    assert speeds["X"] > 0

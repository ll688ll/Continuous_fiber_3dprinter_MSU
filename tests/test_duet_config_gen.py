"""generate_machine_g: the traps are encoded as tests (plan risks 2/3/4/6)."""

from __future__ import annotations

import copy

import pytest

from printer_msu.config.duet_config_gen import generate_machine_g
from printer_msu.config.profile import profile_from_dict

from printer_testlib import DUET_PROFILE_DATA, make_duet_profile


def _lines(profile) -> list[str]:
    return [l for l in generate_machine_g(profile).splitlines()
            if l.strip() and not l.strip().startswith(";")]


def _with_tool_hardware() -> dict:
    data = copy.deepcopy(DUET_PROFILE_DATA)
    data["tools"][0]["extruder"] = {
        "controller": "duet", "channel": 3, "letter": "E",
        "steps_per_mm": 400.0, "max_speed_mm_s": 60.0,
        "max_accel_mm_s2": 500.0, "jerk_mm_s": 2.0}
    data["tools"][0]["heater"] = {
        "controller": "duet", "heater_index": 0, "output_pin": "out0",
        "sensor": {"sensor_index": 0, "type": "thermocouple-max31856",
                   "port": "spi.cs0"},
        "max_temp_c": 300.0}
    return data


def test_single_m584_comes_first_and_before_m574():
    """M584 resets datums and clears M574 — one line, first, always."""
    lines = _lines(make_duet_profile())
    m584s = [i for i, l in enumerate(lines) if l.startswith("M584")]
    assert m584s == [0], "exactly one M584, first functional line"
    m574s = [i for i, l in enumerate(lines) if l.startswith("M574")]
    assert m574s and all(i > m584s[0] for i in m574s)


def test_units_are_rrf_flavour():
    """Profile speaks mm/s; the board wants mm/min for M203/M566."""
    text = generate_machine_g(make_duet_profile())
    assert "M203 X12000 Y12000 Z6000" in text     # 200/200/100 mm/s
    assert "M566 X480 Y480 Z120" in text          # 8/8/2 mm/s jerk
    assert "M201 X1000 Y1000 Z800" in text        # accel stays mm/s^2
    assert "M92 X142.857 Y142.857 Z500" in text


def test_limits_and_endstops():
    text = generate_machine_g(make_duet_profile())
    assert "M208 X0 Y0 Z0 S1" in text
    assert "M208 X987.448 Y290.269 Z282.306 S0" in text
    assert 'M574 X1 S1 P"!io3.in"' in text
    assert 'M574 Z1 S1 P"!io5.in"' in text


def test_never_emits_network_or_serial_config():
    """Static IP stays hand-maintained in config.g; M575 would claim
    io0.in/serial0.rx."""
    text = generate_machine_g(make_duet_profile())
    for forbidden in ("M552", "M553", "M554", "M575", "io0.in"):
        assert forbidden not in text


def test_extruder_joins_the_single_m584_line():
    profile = profile_from_dict(_with_tool_hardware())
    lines = _lines(profile)
    assert lines[0].startswith("M584") and "E3" in lines[0]
    text = "\n".join(lines)
    assert "M92 E400" in text
    assert "M203 E3600" in text  # 60 mm/s -> mm/min


def test_heater_block_defines_tool_cold_with_temp_ceiling():
    profile = profile_from_dict(_with_tool_hardware())
    text = generate_machine_g(profile)
    assert 'M308 S0 P"spi.cs0" Y"thermocouple-max31856"' in text
    assert 'M950 H0 C"out0" T0' in text
    assert "M143 H0 S300" in text          # hard ceiling always present
    assert "M563 P0 D0 H0" in text
    assert "M568 P0 R0 S0" in text         # defined cold


def test_no_duet_axes_raises():
    from printer_testlib import make_profile

    with pytest.raises(ValueError, match="no axes on a duet controller"):
        generate_machine_g(make_profile())  # fmc-only profile


# ------------------------------------------------------------- homing macros
def test_homing_macros_generated_for_each_homable_axis():
    from printer_msu.config.duet_config_gen import generate_homing_macros

    profile = make_duet_profile()
    files = generate_homing_macros(profile)
    assert set(files) == {"homeall.g", "homex.g", "homey.g", "homez.g"}
    x = files["homex.g"]
    # proven pattern: G91 wrap, bounded fast H1, back-off, slow H1, clear
    assert "G91" in x and x.rstrip().endswith("G90")
    assert "G1 H1 X-1050 F1800" in x  # 987.448 span -> bound 1050, 30 mm/s
    assert "G1 H1 X-10 F150" in x     # slow re-approach 2.5 mm/s
    assert x.count("G1 H2 X5 F600") == 3  # 10 mm/s hops for a gantry axis
    z = files["homez.g"]
    assert "G1 H2 Z5 F300" in z       # homes_up axis hops at 5 mm/s
    assert "negative is UP" in z


def test_homeall_orders_tool_z_axes_first():
    from printer_msu.config.duet_config_gen import generate_homing_macros

    files = generate_homing_macros(make_duet_profile())
    calls = [l for l in files["homeall.g"].splitlines() if l.startswith("M98")]
    assert calls == ['M98 P"homez.g"', 'M98 P"homex.g"', 'M98 P"homey.g"']


def test_new_axis_without_endstop_is_skipped_with_note():
    from printer_msu.config.duet_config_gen import generate_homing_macros

    data = copy.deepcopy(DUET_PROFILE_DATA)
    data["axes"].append({
        "name": "Z_T1", "controller": "duet", "channel": 3, "letter": "U",
        "steps_per_mm": 500.0, "max_speed_mm_s": 100.0,
        "max_accel_mm_s2": 800.0, "jerk_mm_s": 2.0,
        "limits_mm": [0.0, 282.0], "homes_up": True,
    })
    files = generate_homing_macros(profile_from_dict(data))
    assert "homeu.g" not in files
    assert "Z_T1 (U) has no minimum endstop" in files["homeall.g"]


def test_new_axis_with_endstop_gets_macro_and_homeall_slot():
    from printer_msu.config.duet_config_gen import generate_homing_macros

    data = copy.deepcopy(DUET_PROFILE_DATA)
    data["axes"].append({
        "name": "Z_T1", "controller": "duet", "channel": 3, "letter": "U",
        "steps_per_mm": 500.0, "max_speed_mm_s": 100.0,
        "max_accel_mm_s2": 800.0, "jerk_mm_s": 2.0,
        "limits_mm": [0.0, 282.0], "homes_up": True,
        "endstop": {"end": "min", "pin": "io6.in", "inverted": True},
    })
    files = generate_homing_macros(profile_from_dict(data))
    assert "G1 H1 U-310 F1800" in files["homeu.g"]
    calls = [l for l in files["homeall.g"].splitlines() if l.startswith("M98")]
    assert calls == ['M98 P"homez.g"', 'M98 P"homeu.g"',
                     'M98 P"homex.g"', 'M98 P"homey.g"']


def test_homing_macros_are_ascii_lf():
    from printer_msu.config.duet_config_gen import generate_homing_macros

    for body in generate_homing_macros(make_duet_profile()).values():
        body.encode("ascii")
        assert "\r" not in body

"""Generate the board-side machine config from a profile (decision D6).

The profile is the source of truth for axis placement and limits; this module
renders it as ``/sys/msu_machine.g``, which the hand-maintained ``config.g``
includes via ``M98 P"/sys/msu_machine.g"``. Hand-maintained concerns (network,
driver step timing, safety commentary) NEVER appear here — in particular no
M552/M553/M554 (the lab subnet has no DHCP; the static IP lives in config.g)
and no M575 (it would claim io0.in/serial0.rx).

Ordering rule that cost a real debugging session (2026-08-25): **M584 resets
axis datums and clears M574**, so exactly ONE M584 line is emitted, first,
carrying every axis and extruder; endstops come after all axis parameters.

Units: the profile speaks mm/s; RRF's M203 (max speed) and M566 (jerk) take
**mm/min**, M201 (accel) takes mm/s^2. The conversions live here and nowhere
else on the write path.
"""

from __future__ import annotations

from .profile import CONTROLLER_DUET, AxisDef, MachineProfile, ToolConfig

BOARD_PATH = "/sys/msu_machine.g"


def _fmt(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _mm_min(value_mm_s: float) -> str:
    return _fmt(value_mm_s * 60.0)


def generate_machine_g(profile: MachineProfile) -> str:
    """Render the generated include for the profile's Duet controller.

    Raises ``ValueError`` if the profile has no duet controller with axes.
    """
    duet_ids = [c.id for c in profile.controllers if c.type == CONTROLLER_DUET]
    axes: list[AxisDef] = []
    for cid in duet_ids:
        axes.extend(profile.axes_on(cid))
    if not axes:
        raise ValueError("profile has no axes on a duet controller")
    axes = sorted(axes, key=lambda a: a.channel)

    tools: list[ToolConfig] = [
        t for t in profile.tools
        if (t.extruder is not None and t.extruder.controller in duet_ids)
        or (t.heater is not None and t.heater.controller in duet_ids)
        or (t.fan is not None and t.fan.controller in duet_ids)
    ]
    extruders = [t.extruder for t in profile.tools
                 if t.extruder is not None and t.extruder.controller in duet_ids]

    lines: list[str] = [
        "; msu_machine.g - GENERATED from profile "
        f"{profile.profile_name!r} - do not hand-edit",
        "; regenerate + upload with: printer-msu apply-config --profile <p>",
        "; (network/static-IP and M569 driver timing stay in config.g)",
        "",
        "; --- axis mapping (single M584: it resets datums and clears M574,",
        ";     so it must come first and appear exactly once) ---",
    ]

    m584 = "M584 " + " ".join(f"{a.letter}{a.channel}" for a in axes)
    if extruders:
        m584 += " E" + ":".join(str(e.channel) for e in extruders)
    lines.append(m584)

    def per_axis(code: str, values: list[str]) -> str:
        return code + " " + " ".join(values)

    lines += [
        "",
        "; --- axis parameters (profile is the source of truth) ---",
        per_axis("M92", [f"{a.letter}{_fmt(a.steps_per_mm)}" for a in axes]),
        per_axis("M566", [f"{a.letter}{_mm_min(a.jerk_mm_s)}" for a in axes])
        + " ; jerk, mm/min",
        per_axis("M203", [f"{a.letter}{_mm_min(a.max_speed_mm_s)}" for a in axes])
        + " ; max speed, mm/min",
        per_axis("M201", [f"{a.letter}{_fmt(a.max_accel_mm_s2)}" for a in axes])
        + " ; accel, mm/s^2",
        per_axis("M208", [f"{a.letter}{_fmt(a.limits_mm[0])}" for a in axes])
        + " S1 ; minima",
        per_axis("M208", [f"{a.letter}{_fmt(a.limits_mm[1])}" for a in axes])
        + " S0 ; maxima",
    ]

    endstop_lines = []
    for a in axes:
        if a.endstop is None or not a.endstop.pin:
            continue
        end_code = 1 if a.endstop.end == "min" else 2
        pin = ("!" if a.endstop.inverted else "") + a.endstop.pin
        endstop_lines.append(f'M574 {a.letter}{end_code} S1 P"{pin}"')
    if endstop_lines:
        lines += ["", "; --- endstops (after M584/M208; the serial0-shared "
                      "input pin is banned at validation) ---"]
        lines += endstop_lines

    if extruders:
        lines += ["", "; --- extruders ---"]
        lines.append(per_axis("M92", [f"E{_fmt(e.steps_per_mm)}"
                                      for e in extruders]))
        lines.append(per_axis("M566", [f"E{_mm_min(e.jerk_mm_s)}"
                                       for e in extruders]) + " ; mm/min")
        lines.append(per_axis("M203", [f"E{_mm_min(e.max_speed_mm_s)}"
                                       for e in extruders]) + " ; mm/min")
        lines.append(per_axis("M201", [f"E{_fmt(e.max_accel_mm_s2)}"
                                       for e in extruders]))

    for tool in tools:
        lines += ["", f"; --- tool T{tool.tool_index} ---"]
        heater = tool.heater
        if heater is not None:
            s = heater.sensor
            lines.append(f'M308 S{s.sensor_index} P"{s.port}" Y"{s.type}"')
            lines.append(f'M950 H{heater.heater_index} C"{heater.output_pin}" '
                         f"T{s.sensor_index}")
            lines.append(f"M143 H{heater.heater_index} "
                         f"S{_fmt(heater.max_temp_c)} ; hard temp ceiling")
        if tool.fan is not None:
            lines.append(f'M950 F{tool.fan.fan_index} '
                         f'C"{tool.fan.output_pin}"')
        m563 = f"M563 P{tool.tool_index}"
        if tool.extruder is not None:
            m563 += f" D{extruders.index(tool.extruder)}"
        if heater is not None:
            m563 += f" H{heater.heater_index}"
        if tool.fan is not None:
            m563 += f" F{tool.fan.fan_index}"
        lines.append(m563)
        if heater is not None:
            lines.append(f"M568 P{tool.tool_index} R0 S0 ; defined cold")

    return "\n".join(lines) + "\n"

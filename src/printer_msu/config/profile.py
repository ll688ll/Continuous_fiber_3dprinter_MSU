"""Machine profile schema + validation (printer PRD v2, FR-PROF-1x).

Schema v2: the profile describes the whole machine — a list of *controllers*
(Duet / fmc_motion), *axes* placed on them (controller + channel + letter +
limits/speeds/endstop), and *tools* that own a Z axis, an optional extruder,
an optional heater/sensor, and a per-tool print-plane offset.

Schema v1 (single fmc_motion controller via ``motion.config`` + ``axis_map``)
is auto-migrated on load; ``to_dict()`` always emits canonical v2 so job-report
snapshots are self-describing.

Only one controller may *own axes* per profile in this phase (decision D-V2-1);
the schema itself is future-open to mixed machines.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional

from ..errors import ProfileError

SCHEMA_VERSION = 2

POLICY_WARN = "warn"
POLICY_ERROR = "error"
POLICY_SKIP = "skip"
POLICY_CLAMP = "clamp"

CONTROLLER_FMC = "fmc_motion"
CONTROLLER_DUET = "duet"
_CONTROLLER_TYPES = (CONTROLLER_FMC, CONTROLLER_DUET)

#: io0.in is physically serial0.rx on the MB6XD — an endstop there reads
#: garbage whenever the UART is enabled (hardware trap, 2026-08-25).
_FORBIDDEN_ENDSTOP_PINS = ("io0.in", "!io0.in")


# --------------------------------------------------------------- controllers
@dataclass(frozen=True)
class ControllerDef:
    id: str
    type: str                      # "fmc_motion" | "duet"
    connection: Mapping[str, Any]  # type-specific, validated in the loader

    # fmc_motion convenience accessors (validated present for that type)
    @property
    def fmc_config_path(self) -> Path:
        return Path(self.connection["config"])

    @property
    def fmc_mode(self) -> str:
        return self.connection.get("mode", "sim")

    # duet convenience accessors
    @property
    def duet_host(self) -> str:
        return self.connection["host"]


# --------------------------------------------------------------------- axes
@dataclass(frozen=True)
class EndstopDef:
    end: str = "min"        # "min" | "max"
    pin: str = ""           # duet io pin, e.g. "io3.in"
    inverted: bool = True


@dataclass(frozen=True)
class AxisDef:
    name: str
    controller: str
    channel: int
    letter: Optional[str] = None          # RRF axis letter; required for duet
    steps_per_mm: Optional[float] = None
    max_speed_mm_s: Optional[float] = None
    max_accel_mm_s2: Optional[float] = None
    jerk_mm_s: Optional[float] = None
    limits_mm: Optional[tuple[float, float]] = None
    endstop: Optional[EndstopDef] = None
    homes_up: bool = False                # tool Z: negative limit is at the TOP


# -------------------------------------------------------------------- tools
@dataclass(frozen=True)
class SensorDef:
    sensor_index: int
    type: str                # RRF M308 Y value, e.g. "thermocouple-max31856"
    port: str                # RRF M308 P value, e.g. "spi.cs0"


@dataclass(frozen=True)
class HeaterDef:
    controller: str
    heater_index: int
    output_pin: str          # RRF M950 C value, e.g. "out0"
    sensor: SensorDef
    max_temp_c: float


@dataclass(frozen=True)
class ExtruderDef:
    controller: str
    channel: int
    steps_per_mm: float
    max_speed_mm_s: float
    max_accel_mm_s2: float
    letter: str = "E"
    jerk_mm_s: float = 2.0


@dataclass(frozen=True)
class ToolConfig:
    tool_index: int
    z_axis: Optional[str] = None       # logical axis name; None = no Z wired
    #: machine-Z of the print plane for this tool (machine zero at the TOP:
    #: machine_z = plane_offset_mm - gcode_z). Set by calibration.
    plane_offset_mm: Optional[float] = None
    extruder: Optional[ExtruderDef] = None
    heater: Optional[HeaterDef] = None


# ---------------------------------------------------- v1 compatibility view
@dataclass(frozen=True)
class MotionConfig:
    """Legacy single-fmc view, kept for the FMC adapter path and v1 callers.

    Present only when the profile has exactly one controller and it is
    ``fmc_motion``; ``None`` on Duet profiles.
    """

    config_path: Path
    mode: str = "sim"
    axis_map: Mapping[str, int] = field(default_factory=dict)


# ----------------------------------------------------------------- dialect
@dataclass(frozen=True)
class GcodeDialect:
    default_tool: int = 0
    default_feed_mm_min: float = 600.0
    travel_feed_mm_min: Optional[float] = None  # G0 feed; None -> modal/default


Z_MODE_MACHINE = "machine"
Z_MODE_BED = "bed"


@dataclass(frozen=True)
class Workspace:
    """Maps slicer coordinates into the machine frame (FR-INT-08).

    ``xy_offset`` shifts absolute X/Y targets (slicer bed origin -> machine).
    ``z_mode``: "machine" passes Z through raw; "bed" treats G-code Z as height
    above the print plane on a machine whose zero is at the TOP:
    ``machine_z = tool.plane_offset_mm - gcode_z`` (relative Z deltas negate).
    The plane offset is per tool (schema v2); v1's global ``z_bed_offset_mm``
    migrates onto every tool with a Z axis.
    """

    xy_offset: Mapping[str, float] = field(default_factory=dict)
    z_mode: str = Z_MODE_MACHINE


EXECUTION_PRELOAD = "preload"
EXECUTION_STREAM = "stream"


@dataclass(frozen=True)
class Policies:
    unknown_command: str = POLICY_WARN      # warn | error
    tool_commands: str = POLICY_SKIP        # skip | error
    missing_axis: str = POLICY_ERROR        # error | skip
    feed_over_max: str = POLICY_CLAMP       # clamp | error
    require_homed: bool = True
    home_before_print: bool = False
    #: moves dispatched ahead into the controller queues (FMC path only).
    stream_window: int = 4
    #: G2/G3 handling: "auto" = accept when the backend supports arcs (D5);
    #: "error" = always reject (the FMC-era D4 behavior).
    arcs: str = "auto"
    #: Duet job execution: preload (rr_upload + M32) or stream (batched rr_gcode).
    execution: str = EXECUTION_PRELOAD
    #: board-config drift check on connect: error | warn.
    config_drift: str = POLICY_ERROR


# ------------------------------------------------------------------ profile
@dataclass(frozen=True)
class MachineProfile:
    profile_name: str
    controllers: tuple[ControllerDef, ...]
    axes: tuple[AxisDef, ...]
    tools: tuple[ToolConfig, ...]
    gcode: GcodeDialect = field(default_factory=GcodeDialect)
    policies: Policies = field(default_factory=Policies)
    workspace: Workspace = field(default_factory=Workspace)
    #: legacy view; present iff the single controller is fmc_motion
    motion: Optional[MotionConfig] = None
    #: non-fatal validator observations (surfaced by preflight/CLI)
    validation_warnings: tuple[str, ...] = ()

    # -- lookups ----------------------------------------------------------
    def tool(self, index: int) -> Optional[ToolConfig]:
        for t in self.tools:
            if t.tool_index == index:
                return t
        return None

    def controller(self, controller_id: str) -> Optional[ControllerDef]:
        for c in self.controllers:
            if c.id == controller_id:
                return c
        return None

    def axis(self, name: str) -> Optional[AxisDef]:
        for a in self.axes:
            if a.name == name:
                return a
        return None

    def has_axis(self, name: str) -> bool:
        return self.axis(name) is not None

    @property
    def axis_names(self) -> tuple[str, ...]:
        return tuple(a.name for a in self.axes)

    def axes_on(self, controller_id: str) -> tuple[AxisDef, ...]:
        return tuple(a for a in self.axes if a.controller == controller_id)

    @property
    def motion_controller(self) -> ControllerDef:
        """The single controller that owns axes (validated exactly one)."""
        owner_ids = {a.controller for a in self.axes}
        # validation guarantees len == 1 for loadable profiles
        return self.controller(sorted(owner_ids)[0])  # type: ignore[return-value]

    @property
    def is_sim(self) -> bool:
        return self.motion is not None and self.motion.mode == "sim"

    @property
    def mode_label(self) -> str:
        """Human label for reports/UI: 'sim'/'real' for fmc, 'duet' otherwise."""
        if self.motion is not None:
            return self.motion.mode
        return self.motion_controller.type

    # -- snapshot ---------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        """Canonical v2 snapshot for job reports and profile saving."""

        def axis_dict(a: AxisDef) -> dict[str, Any]:
            d: dict[str, Any] = {"name": a.name, "controller": a.controller,
                                 "channel": a.channel}
            if a.letter is not None:
                d["letter"] = a.letter
            for key, value in (("steps_per_mm", a.steps_per_mm),
                               ("max_speed_mm_s", a.max_speed_mm_s),
                               ("max_accel_mm_s2", a.max_accel_mm_s2),
                               ("jerk_mm_s", a.jerk_mm_s)):
                if value is not None:
                    d[key] = value
            if a.limits_mm is not None:
                d["limits_mm"] = list(a.limits_mm)
            if a.endstop is not None:
                d["endstop"] = {"end": a.endstop.end, "pin": a.endstop.pin,
                                "inverted": a.endstop.inverted}
            if a.homes_up:
                d["homes_up"] = True
            return d

        def tool_dict(t: ToolConfig) -> dict[str, Any]:
            d: dict[str, Any] = {"tool_index": t.tool_index, "z_axis": t.z_axis}
            if t.plane_offset_mm is not None:
                d["plane_offset_mm"] = t.plane_offset_mm
            if t.extruder is not None:
                e = t.extruder
                d["extruder"] = {"controller": e.controller, "channel": e.channel,
                                 "letter": e.letter, "steps_per_mm": e.steps_per_mm,
                                 "max_speed_mm_s": e.max_speed_mm_s,
                                 "max_accel_mm_s2": e.max_accel_mm_s2,
                                 "jerk_mm_s": e.jerk_mm_s}
            if t.heater is not None:
                h = t.heater
                d["heater"] = {"controller": h.controller,
                               "heater_index": h.heater_index,
                               "output_pin": h.output_pin,
                               "sensor": {"sensor_index": h.sensor.sensor_index,
                                          "type": h.sensor.type,
                                          "port": h.sensor.port},
                               "max_temp_c": h.max_temp_c}
            return d

        return {
            "schema_version": SCHEMA_VERSION,
            "profile_name": self.profile_name,
            "controllers": [
                {"id": c.id, "type": c.type,
                 "connection": {k: (str(v) if isinstance(v, Path) else v)
                                for k, v in c.connection.items()}}
                for c in self.controllers
            ],
            "axes": [axis_dict(a) for a in self.axes],
            "tools": [tool_dict(t) for t in self.tools],
            "gcode": {
                "default_tool": self.gcode.default_tool,
                "default_feed_mm_min": self.gcode.default_feed_mm_min,
                "travel_feed_mm_min": self.gcode.travel_feed_mm_min,
            },
            "workspace": {
                "xy_offset": dict(self.workspace.xy_offset),
                "z_mode": self.workspace.z_mode,
            },
            "policies": {
                "unknown_command": self.policies.unknown_command,
                "tool_commands": self.policies.tool_commands,
                "missing_axis": self.policies.missing_axis,
                "feed_over_max": self.policies.feed_over_max,
                "require_homed": self.policies.require_homed,
                "home_before_print": self.policies.home_before_print,
                "stream_window": self.policies.stream_window,
                "arcs": self.policies.arcs,
                "execution": self.policies.execution,
                "config_drift": self.policies.config_drift,
            },
        }


# -------------------------------------------------------------- validation
def _choice(section: str, key: str, value: str, allowed: tuple[str, ...]) -> str:
    if value not in allowed:
        raise ProfileError(f"{section}.{key}={value!r}; expected one of {allowed}")
    return value


def _require(mapping: Mapping[str, Any], key: str, context: str) -> Any:
    if key not in mapping:
        raise ProfileError(f"{context}: missing required key {key!r}")
    return mapping[key]


def _positive(value: Any, context: str) -> float:
    v = float(value)
    if v <= 0:
        raise ProfileError(f"{context} must be > 0 (got {value!r})")
    return v


def _migrate_v1(data: Mapping[str, Any]) -> dict[str, Any]:
    """Schema v1 (motion.config + axis_map) -> canonical v2 dict."""
    try:
        motion_raw = data["motion"]
        axis_map = dict(motion_raw["axis_map"])
        config = motion_raw["config"]
    except KeyError as exc:
        raise ProfileError(f"profile missing required key: {exc}") from None

    workspace = dict(data.get("workspace", {}))
    z_bed_offset = workspace.pop("z_bed_offset_mm", None)

    tools_raw = [dict(t) for t in data.get("tools",
                                           [{"tool_index": 0, "z_axis": None}])]
    for t in tools_raw:
        if t.get("z_axis") is not None and z_bed_offset is not None:
            t.setdefault("plane_offset_mm", z_bed_offset)

    return {
        "schema_version": SCHEMA_VERSION,
        "profile_name": data.get("profile_name"),
        "controllers": [{
            "id": "fmc",
            "type": CONTROLLER_FMC,
            "connection": {"config": config,
                           "mode": motion_raw.get("mode", "sim")},
        }],
        "axes": [{"name": name, "controller": "fmc", "channel": index}
                 for name, index in axis_map.items()],
        "tools": tools_raw,
        "gcode": data.get("gcode", {}),
        "workspace": workspace,
        "policies": data.get("policies", {}),
    }


def _parse_controller(raw: Mapping[str, Any], base_dir: Path | None) -> ControllerDef:
    cid = _require(raw, "id", "controller")
    ctype = _choice(f"controller {cid!r}", "type",
                    _require(raw, "type", f"controller {cid!r}"),
                    _CONTROLLER_TYPES)
    conn = dict(_require(raw, "connection", f"controller {cid!r}"))
    if ctype == CONTROLLER_FMC:
        config_path = Path(_require(conn, "config", f"controller {cid!r}.connection"))
        if base_dir is not None and not config_path.is_absolute():
            config_path = (base_dir / config_path).resolve()
        conn["config"] = str(config_path)
        _choice(f"controller {cid!r}.connection", "mode",
                conn.get("mode", "sim"), ("sim", "real"))
    else:  # duet
        host = _require(conn, "host", f"controller {cid!r}.connection")
        if not isinstance(host, str) or not host:
            raise ProfileError(f"controller {cid!r}: connection.host must be a "
                               f"non-empty string")
        conn.setdefault("password", "")
        conn["http_timeout_s"] = _positive(conn.get("http_timeout_s", 20.0),
                                           f"controller {cid!r}.connection.http_timeout_s")
    return ControllerDef(id=cid, type=ctype, connection=conn)


def _parse_axis(raw: Mapping[str, Any], controllers: Mapping[str, ControllerDef],
                ) -> AxisDef:
    name = _require(raw, "name", "axis")
    ctx = f"axis {name!r}"
    controller_id = _require(raw, "controller", ctx)
    if controller_id not in controllers:
        raise ProfileError(f"{ctx}: controller {controller_id!r} is not defined")
    channel = _require(raw, "channel", ctx)
    if not isinstance(channel, int) or channel < 0:
        raise ProfileError(f"{ctx}: channel must be an integer >= 0")

    endstop = None
    if raw.get("endstop") is not None:
        e = raw["endstop"]
        pin = e.get("pin", "")
        if pin.lstrip("!") in [p.lstrip("!") for p in _FORBIDDEN_ENDSTOP_PINS]:
            raise ProfileError(
                f"{ctx}: endstop pin {pin!r} is forbidden — io0.in is shared "
                f"with serial0.rx on the MB6XD and cannot be an endstop")
        endstop = EndstopDef(
            end=_choice(f"{ctx}.endstop", "end", e.get("end", "min"),
                        ("min", "max")),
            pin=pin,
            inverted=bool(e.get("inverted", True)),
        )

    limits = None
    if raw.get("limits_mm") is not None:
        lo, hi = raw["limits_mm"]
        if float(lo) >= float(hi):
            raise ProfileError(f"{ctx}: limits_mm min must be < max")
        limits = (float(lo), float(hi))

    axis = AxisDef(
        name=name,
        controller=controller_id,
        channel=channel,
        letter=raw.get("letter"),
        steps_per_mm=(_positive(raw["steps_per_mm"], f"{ctx}.steps_per_mm")
                      if raw.get("steps_per_mm") is not None else None),
        max_speed_mm_s=(_positive(raw["max_speed_mm_s"], f"{ctx}.max_speed_mm_s")
                        if raw.get("max_speed_mm_s") is not None else None),
        max_accel_mm_s2=(_positive(raw["max_accel_mm_s2"], f"{ctx}.max_accel_mm_s2")
                         if raw.get("max_accel_mm_s2") is not None else None),
        jerk_mm_s=(_positive(raw["jerk_mm_s"], f"{ctx}.jerk_mm_s")
                   if raw.get("jerk_mm_s") is not None else None),
        limits_mm=limits,
        endstop=endstop,
        homes_up=bool(raw.get("homes_up", False)),
    )

    # duet axes must be fully specified — the board config is generated from them
    if controllers[controller_id].type == CONTROLLER_DUET:
        if not axis.letter or len(axis.letter) != 1 or not axis.letter.isalpha():
            raise ProfileError(f"{ctx}: duet axes need a single-letter 'letter'")
        for key in ("steps_per_mm", "max_speed_mm_s", "max_accel_mm_s2",
                    "jerk_mm_s"):
            if getattr(axis, key) is None:
                raise ProfileError(f"{ctx}: {key} is required for duet axes")
        if axis.limits_mm is None:
            raise ProfileError(f"{ctx}: limits_mm is required for duet axes")
        if not 0 <= channel <= 5:
            raise ProfileError(f"{ctx}: duet driver channel must be 0..5")
    return axis


def _parse_tool(raw: Mapping[str, Any], axes: Mapping[str, AxisDef],
                controllers: Mapping[str, ControllerDef],
                warnings: list[str]) -> ToolConfig:
    idx = _require(raw, "tool_index", "tool")
    ctx = f"tool {idx}"
    z_axis = raw.get("z_axis")
    if z_axis is not None and z_axis not in axes:
        raise ProfileError(f"{ctx}: z_axis {z_axis!r} is not a configured axis")

    extruder = None
    if raw.get("extruder") is not None:
        e = raw["extruder"]
        e_ctrl = _require(e, "controller", f"{ctx}.extruder")
        if e_ctrl not in controllers:
            raise ProfileError(f"{ctx}.extruder: controller {e_ctrl!r} not defined")
        extruder = ExtruderDef(
            controller=e_ctrl,
            channel=int(_require(e, "channel", f"{ctx}.extruder")),
            letter=e.get("letter", "E"),
            steps_per_mm=_positive(_require(e, "steps_per_mm", f"{ctx}.extruder"),
                                   f"{ctx}.extruder.steps_per_mm"),
            max_speed_mm_s=_positive(_require(e, "max_speed_mm_s", f"{ctx}.extruder"),
                                     f"{ctx}.extruder.max_speed_mm_s"),
            max_accel_mm_s2=_positive(_require(e, "max_accel_mm_s2", f"{ctx}.extruder"),
                                      f"{ctx}.extruder.max_accel_mm_s2"),
            jerk_mm_s=float(e.get("jerk_mm_s", 2.0)),
        )
        if z_axis is not None and axes[z_axis].controller != e_ctrl:
            # planner sync needs E on the same board as the motion it rides
            warnings.append(
                f"{ctx}: extruder is on controller {e_ctrl!r} but z_axis "
                f"{z_axis!r} is on {axes[z_axis].controller!r} — extrusion "
                f"cannot be planner-synced across controllers")

    heater = None
    if raw.get("heater") is not None:
        h = raw["heater"]
        h_ctrl = _require(h, "controller", f"{ctx}.heater")
        if h_ctrl not in controllers:
            raise ProfileError(f"{ctx}.heater: controller {h_ctrl!r} not defined")
        s = _require(h, "sensor", f"{ctx}.heater")
        heater = HeaterDef(
            controller=h_ctrl,
            heater_index=int(_require(h, "heater_index", f"{ctx}.heater")),
            output_pin=str(_require(h, "output_pin", f"{ctx}.heater")),
            sensor=SensorDef(
                sensor_index=int(_require(s, "sensor_index", f"{ctx}.heater.sensor")),
                type=str(_require(s, "type", f"{ctx}.heater.sensor")),
                port=str(_require(s, "port", f"{ctx}.heater.sensor")),
            ),
            max_temp_c=_positive(_require(h, "max_temp_c", f"{ctx}.heater"),
                                 f"{ctx}.heater.max_temp_c"),
        )

    plane = (float(raw["plane_offset_mm"])
             if raw.get("plane_offset_mm") is not None else None)
    return ToolConfig(tool_index=idx, z_axis=z_axis, plane_offset_mm=plane,
                      extruder=extruder, heater=heater)


def profile_from_dict(data: Mapping[str, Any],
                      base_dir: Path | None = None) -> MachineProfile:
    """Build + validate a profile; ``base_dir`` resolves relative paths.

    Accepts schema v1 (auto-migrated) and v2.
    """
    version = data.get("schema_version", 1)
    if version == 1:
        data = _migrate_v1(data)
    elif version != SCHEMA_VERSION:
        raise ProfileError(f"unsupported schema_version {version!r}")

    name = data.get("profile_name")
    if not name:
        raise ProfileError("profile missing required key: 'profile_name'")

    warnings: list[str] = []

    controllers_raw = data.get("controllers") or []
    if not controllers_raw:
        raise ProfileError("profile must define at least one controller")
    controllers: dict[str, ControllerDef] = {}
    for raw in controllers_raw:
        c = _parse_controller(raw, base_dir)
        if c.id in controllers:
            raise ProfileError(f"duplicate controller id {c.id!r}")
        controllers[c.id] = c

    axes_raw = data.get("axes") or []
    if not axes_raw:
        raise ProfileError("profile must define at least one axis")
    axes: dict[str, AxisDef] = {}
    seen_channels: set[tuple[str, int]] = set()
    seen_letters: set[tuple[str, str]] = set()
    for raw in axes_raw:
        a = _parse_axis(raw, controllers)
        if a.name in axes:
            raise ProfileError(f"duplicate axis name {a.name!r}")
        chan_key = (a.controller, a.channel)
        if chan_key in seen_channels:
            raise ProfileError(
                f"axis {a.name!r}: channel {a.channel} on controller "
                f"{a.controller!r} is already assigned")
        seen_channels.add(chan_key)
        if a.letter is not None:
            letter_key = (a.controller, a.letter.upper())
            if letter_key in seen_letters:
                raise ProfileError(
                    f"axis {a.name!r}: letter {a.letter!r} on controller "
                    f"{a.controller!r} is already assigned")
            seen_letters.add(letter_key)
        axes[a.name] = a

    tools: list[ToolConfig] = []
    seen_tools: set[int] = set()
    for raw in data.get("tools", [{"tool_index": 0, "z_axis": None}]):
        t = _parse_tool(raw, axes, controllers, warnings)
        if t.tool_index in seen_tools:
            raise ProfileError(f"duplicate tool_index {t.tool_index}")
        seen_tools.add(t.tool_index)
        # extruder channels must not collide with axis channels on the same board
        if t.extruder is not None:
            e_key = (t.extruder.controller, t.extruder.channel)
            if e_key in seen_channels:
                raise ProfileError(
                    f"tool {t.tool_index}: extruder channel {t.extruder.channel} "
                    f"on controller {t.extruder.controller!r} is already assigned")
            seen_channels.add(e_key)
        tools.append(t)
    if not tools:
        raise ProfileError("profile must define at least one tool")

    g = data.get("gcode", {})
    dialect = GcodeDialect(
        default_tool=g.get("default_tool", 0),
        default_feed_mm_min=float(g.get("default_feed_mm_min", 600.0)),
        travel_feed_mm_min=(float(g["travel_feed_mm_min"])
                            if g.get("travel_feed_mm_min") is not None else None),
    )
    if dialect.default_feed_mm_min <= 0:
        raise ProfileError("gcode.default_feed_mm_min must be > 0")
    if not any(t.tool_index == dialect.default_tool for t in tools):
        raise ProfileError(
            f"gcode.default_tool={dialect.default_tool} is not a configured tool")

    w = data.get("workspace", {})
    xy_offset = dict(w.get("xy_offset", {}))
    for axis in xy_offset:
        if axis not in ("X", "Y"):
            raise ProfileError(f"workspace.xy_offset[{axis!r}]: only X/Y allowed")
        if axis not in axes:
            raise ProfileError(f"workspace.xy_offset[{axis!r}]: axis not configured")
    z_mode = _choice("workspace", "z_mode", w.get("z_mode", Z_MODE_MACHINE),
                     (Z_MODE_MACHINE, Z_MODE_BED))
    if w.get("z_bed_offset_mm") is not None:
        raise ProfileError(
            "workspace.z_bed_offset_mm was replaced by per-tool plane_offset_mm "
            "in schema v2 (v1 profiles migrate automatically)")
    if z_mode == Z_MODE_BED:
        for t in tools:
            if t.z_axis is not None and t.plane_offset_mm is None:
                raise ProfileError(
                    f"workspace.z_mode='bed' requires plane_offset_mm on every "
                    f"tool with a z_axis (tool {t.tool_index} has none — run "
                    f"plane calibration; v1 name: workspace.z_bed_offset_mm)")
    workspace = Workspace(xy_offset=xy_offset, z_mode=z_mode)

    p = data.get("policies", {})
    policies = Policies(
        unknown_command=_choice("policies", "unknown_command",
                                p.get("unknown_command", POLICY_WARN),
                                (POLICY_WARN, POLICY_ERROR)),
        tool_commands=_choice("policies", "tool_commands",
                              p.get("tool_commands", POLICY_SKIP),
                              (POLICY_SKIP, POLICY_ERROR)),
        missing_axis=_choice("policies", "missing_axis",
                             p.get("missing_axis", POLICY_ERROR),
                             (POLICY_ERROR, POLICY_SKIP)),
        feed_over_max=_choice("policies", "feed_over_max",
                              p.get("feed_over_max", POLICY_CLAMP),
                              (POLICY_CLAMP, POLICY_ERROR)),
        require_homed=bool(p.get("require_homed", True)),
        home_before_print=bool(p.get("home_before_print", False)),
        stream_window=int(p.get("stream_window", 4)),
        arcs=_choice("policies", "arcs", p.get("arcs", "auto"),
                     ("auto", POLICY_ERROR)),
        execution=_choice("policies", "execution",
                          p.get("execution", EXECUTION_PRELOAD),
                          (EXECUTION_PRELOAD, EXECUTION_STREAM)),
        config_drift=_choice("policies", "config_drift",
                             p.get("config_drift", POLICY_ERROR),
                             (POLICY_ERROR, POLICY_WARN)),
    )
    if not 1 <= policies.stream_window <= 32:
        raise ProfileError(
            f"policies.stream_window={policies.stream_window}; expected 1..32")

    # exactly one controller owns axes in this phase (D-V2-1); the schema may
    # describe more, but a loadable profile keeps live motion on one board
    owner_ids = {a.controller for a in axes.values()}
    if len(owner_ids) > 1:
        raise ProfileError(
            f"axes span controllers {sorted(owner_ids)} — driving axes on more "
            f"than one controller is not supported this phase (D-V2-1)")

    # legacy view for the FMC path
    motion: Optional[MotionConfig] = None
    owner = controllers[next(iter(owner_ids))]
    if owner.type == CONTROLLER_FMC:
        motion = MotionConfig(
            config_path=owner.fmc_config_path,
            mode=owner.fmc_mode,
            axis_map={a.name: a.channel for a in axes.values()},
        )

    return MachineProfile(
        profile_name=name,
        controllers=tuple(controllers.values()),
        axes=tuple(axes.values()),
        tools=tuple(tools),
        gcode=dialect,
        policies=policies,
        workspace=workspace,
        motion=motion,
        validation_warnings=tuple(warnings),
    )


def load_profile(path: str | Path) -> MachineProfile:
    p = Path(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ProfileError(f"profile not found: {p}") from None
    except json.JSONDecodeError as exc:
        raise ProfileError(f"profile {p} is not valid JSON: {exc}") from None
    return profile_from_dict(data, base_dir=p.parent)

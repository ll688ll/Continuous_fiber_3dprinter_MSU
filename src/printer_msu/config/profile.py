"""Machine profile schema + validation (printer PRD FR-PROF-01..03).

The profile tells the printer *what the machine is*: which fmc_motion config
to load, how logical axis names map to controller axis indices, which z-axis
each tool owns, and the dialect/policy knobs. Motion limits themselves live in
the fmc_motion config — single source of truth, not duplicated here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional

from ..errors import ProfileError

POLICY_WARN = "warn"
POLICY_ERROR = "error"
POLICY_SKIP = "skip"
POLICY_CLAMP = "clamp"


@dataclass(frozen=True)
class ToolConfig:
    tool_index: int
    z_axis: Optional[str] = None  # logical axis name; None = tool has no Z wired


@dataclass(frozen=True)
class MotionConfig:
    config_path: Path            # fmc_motion controller config (resolved)
    mode: str = "sim"            # "sim" | "real"
    axis_map: Mapping[str, int] = field(default_factory=dict)  # logical -> index


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
    ``z_mode``: "machine" passes Z through raw; "bed" treats G-code Z as
    height above the print plane on a machine whose zero is at the TOP:
    ``machine_z = z_bed_offset_mm - gcode_z`` (relative Z deltas negate).
    """

    xy_offset: Mapping[str, float] = field(default_factory=dict)
    z_mode: str = Z_MODE_MACHINE
    z_bed_offset_mm: Optional[float] = None


@dataclass(frozen=True)
class Policies:
    unknown_command: str = POLICY_WARN      # warn | error
    tool_commands: str = POLICY_SKIP        # skip | error   (motion-only, D2)
    missing_axis: str = POLICY_ERROR        # error | skip
    feed_over_max: str = POLICY_CLAMP       # clamp | error
    require_homed: bool = True
    home_before_print: bool = False
    #: moves dispatched ahead into the controller queues (1 = fully blocking
    #: per segment). Queuing hides the real-mode motion-start grace behind
    #: actual motion, which is most of the per-segment overhead.
    stream_window: int = 4


@dataclass(frozen=True)
class MachineProfile:
    profile_name: str
    motion: MotionConfig
    tools: tuple[ToolConfig, ...]
    gcode: GcodeDialect = field(default_factory=GcodeDialect)
    policies: Policies = field(default_factory=Policies)
    workspace: Workspace = field(default_factory=Workspace)

    def tool(self, index: int) -> Optional[ToolConfig]:
        for t in self.tools:
            if t.tool_index == index:
                return t
        return None

    def to_dict(self) -> dict[str, Any]:
        """Snapshot for job reports (FR-PROF-03)."""
        return {
            "profile_name": self.profile_name,
            "motion": {
                "config": str(self.motion.config_path),
                "mode": self.motion.mode,
                "axis_map": dict(self.motion.axis_map),
            },
            "tools": [{"tool_index": t.tool_index, "z_axis": t.z_axis}
                      for t in self.tools],
            "gcode": {
                "default_tool": self.gcode.default_tool,
                "default_feed_mm_min": self.gcode.default_feed_mm_min,
                "travel_feed_mm_min": self.gcode.travel_feed_mm_min,
            },
            "workspace": {
                "xy_offset": dict(self.workspace.xy_offset),
                "z_mode": self.workspace.z_mode,
                "z_bed_offset_mm": self.workspace.z_bed_offset_mm,
            },
            "policies": {
                "unknown_command": self.policies.unknown_command,
                "tool_commands": self.policies.tool_commands,
                "missing_axis": self.policies.missing_axis,
                "feed_over_max": self.policies.feed_over_max,
                "require_homed": self.policies.require_homed,
                "home_before_print": self.policies.home_before_print,
                "stream_window": self.policies.stream_window,
            },
        }


def _choice(section: str, key: str, value: str, allowed: tuple[str, ...]) -> str:
    if value not in allowed:
        raise ProfileError(f"{section}.{key}={value!r}; expected one of {allowed}")
    return value


def profile_from_dict(data: Mapping[str, Any],
                      base_dir: Path | None = None) -> MachineProfile:
    """Build + validate a profile; ``base_dir`` resolves relative paths."""
    try:
        name = data["profile_name"]
        motion_raw = data["motion"]
        axis_map = dict(motion_raw["axis_map"])
        config_path = Path(motion_raw["config"])
        tools_raw = data.get("tools", [{"tool_index": 0, "z_axis": None}])
    except KeyError as exc:
        raise ProfileError(f"profile missing required key: {exc}") from None

    if base_dir is not None and not config_path.is_absolute():
        config_path = (base_dir / config_path).resolve()

    mode = _choice("motion", "mode", motion_raw.get("mode", "sim"), ("sim", "real"))

    if not axis_map:
        raise ProfileError("motion.axis_map must map at least one axis")
    for logical, index in axis_map.items():
        if not isinstance(index, int) or index < 0:
            raise ProfileError(f"axis_map[{logical!r}]={index!r}: index must be >= 0")
    indices = list(axis_map.values())
    if len(set(indices)) != len(indices):
        raise ProfileError(f"axis_map has duplicate controller indices: {indices}")

    tools: list[ToolConfig] = []
    seen_tools: set[int] = set()
    for t in tools_raw:
        idx = t["tool_index"]
        if idx in seen_tools:
            raise ProfileError(f"duplicate tool_index {idx}")
        seen_tools.add(idx)
        z_axis = t.get("z_axis")
        if z_axis is not None and z_axis not in axis_map:
            raise ProfileError(
                f"tool {idx}: z_axis {z_axis!r} is not in motion.axis_map"
            )
        tools.append(ToolConfig(tool_index=idx, z_axis=z_axis))
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
            f"gcode.default_tool={dialect.default_tool} is not a configured tool"
        )

    w = data.get("workspace", {})
    xy_offset = dict(w.get("xy_offset", {}))
    for axis in xy_offset:
        if axis not in ("X", "Y"):
            raise ProfileError(f"workspace.xy_offset[{axis!r}]: only X/Y allowed")
        if axis not in axis_map:
            raise ProfileError(f"workspace.xy_offset[{axis!r}]: axis not mapped")
    z_mode = _choice("workspace", "z_mode", w.get("z_mode", Z_MODE_MACHINE),
                     (Z_MODE_MACHINE, Z_MODE_BED))
    z_bed_offset = (float(w["z_bed_offset_mm"])
                    if w.get("z_bed_offset_mm") is not None else None)
    if z_mode == Z_MODE_BED and z_bed_offset is None:
        raise ProfileError("workspace.z_mode='bed' requires z_bed_offset_mm")
    workspace = Workspace(xy_offset=xy_offset, z_mode=z_mode,
                          z_bed_offset_mm=z_bed_offset)

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
    )
    if not 1 <= policies.stream_window <= 32:
        raise ProfileError(
            f"policies.stream_window={policies.stream_window}; expected 1..32")

    return MachineProfile(profile_name=name,
                          motion=MotionConfig(config_path=config_path, mode=mode,
                                              axis_map=axis_map),
                          tools=tuple(tools), gcode=dialect, policies=policies,
                          workspace=workspace)


def load_profile(path: str | Path) -> MachineProfile:
    p = Path(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ProfileError(f"profile not found: {p}") from None
    except json.JSONDecodeError as exc:
        raise ProfileError(f"profile {p} is not valid JSON: {exc}") from None
    return profile_from_dict(data, base_dir=p.parent)

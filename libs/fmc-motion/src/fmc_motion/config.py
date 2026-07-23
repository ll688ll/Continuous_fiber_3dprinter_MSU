"""Configuration schema for fmc_motion (PRD FR-CFG-01..04).

All engineering units are native controller units: mm, mm/s, mm/s^2.
Config files may be JSON (stdlib) or YAML (requires the optional PyYAML
dependency, ``pip install fmc-motion[yaml]``).
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .errors import ConfigError

MAX_AXES = 6
AXIS_NAMES_DEFAULT = ("X", "Y", "Z", "X1", "Y1", "Z1")

HOMING_NEGATIVE = "negative"
HOMING_POSITIVE = "positive"


@dataclass
class ConnectionConfig:
    device_id: int = 1
    ip: str = "192.168.0.30"
    port: int = 8088
    tcp_mode: int = 0  # 0 = blocking TCP, 1 = non-blocking (vendor manual)


@dataclass
class HomingConfig:
    direction: str = HOMING_NEGATIVE  # which limit switch is machine zero
    speed: float = 20.0
    accdec: float = 200.0
    fall_step: float = 2.0  # vendor back-off distance after the switch trips


@dataclass
class MotionParams:
    speed: float = 100.0
    accel: float = 500.0
    decel: float = 500.0


@dataclass
class SelfCheckConfig:
    creep_speed: float = 10.0
    max_search_mm: float = 1000.0
    margin_mm: float = 2.0
    return_to_min: bool = True  # finish self-check parked at soft_min


@dataclass
class JogConfig:
    speed: float = 20.0
    max_step_unhomed: float = 5.0


@dataclass
class SoftLimits:
    """Working range in the homed coordinate frame (0 = home switch trip point)."""

    min: Optional[float] = None
    max: Optional[float] = None
    span_mm: Optional[float] = None       # discovered by self_check
    discovered_at: Optional[str] = None   # ISO timestamp of discovery

    @property
    def known(self) -> bool:
        return self.min is not None and self.max is not None


@dataclass
class AxisConfig:
    index: int = 0
    name: str = ""
    enabled: bool = True
    homing: HomingConfig = field(default_factory=HomingConfig)
    defaults: MotionParams = field(default_factory=MotionParams)
    max: MotionParams = field(default_factory=lambda: MotionParams(300.0, 2000.0, 2000.0))
    self_check: SelfCheckConfig = field(default_factory=SelfCheckConfig)
    jog: JogConfig = field(default_factory=JogConfig)
    allow_unhomed: bool = False
    soft_limits: SoftLimits = field(default_factory=SoftLimits)
    move_timeout_s: Optional[float] = None  # watchdog, real seconds; None = no watchdog


@dataclass
class SimAxisGeometry:
    """Virtual actuator geometry for the sim backend (FR-SIM-03)."""

    travel_mm: float = 200.0        # distance between the two limit-switch trip points
    start_pos_mm: Optional[float] = None  # power-on position between switches; default mid-travel
    overtravel_mm: float = 2.0      # physical travel available past a switch trip point


@dataclass
class SimConfig:
    time_scale: float = 1.0
    axes: dict[int, SimAxisGeometry] = field(default_factory=dict)

    def geometry(self, index: int) -> SimAxisGeometry:
        return self.axes.get(index, SimAxisGeometry())


@dataclass
class ControllerConfig:
    connection: ConnectionConfig = field(default_factory=ConnectionConfig)
    axes: list[AxisConfig] = field(default_factory=list)
    poll_interval_s: float = 0.05
    sim: SimConfig = field(default_factory=SimConfig)
    source_path: Optional[str] = None  # set when loaded from disk

    # ------------------------------------------------------------- validation
    def validate(self) -> None:
        if not self.axes:
            raise ConfigError("config must define at least one axis")
        if self.poll_interval_s <= 0:
            raise ConfigError("poll_interval_s must be > 0")
        seen: set[int] = set()
        for ax in self.axes:
            prefix = f"axis {ax.index!r} ({ax.name or 'unnamed'})"
            if not isinstance(ax.index, int) or not 0 <= ax.index < MAX_AXES:
                raise ConfigError(f"{prefix}: index must be an integer in 0..{MAX_AXES - 1}")
            if ax.index in seen:
                raise ConfigError(f"duplicate axis index {ax.index}")
            seen.add(ax.index)
            if ax.homing.direction not in (HOMING_NEGATIVE, HOMING_POSITIVE):
                raise ConfigError(
                    f"{prefix}: homing.direction must be '{HOMING_NEGATIVE}' or '{HOMING_POSITIVE}'"
                )
            for label, params in (("defaults", ax.defaults), ("max", ax.max)):
                for attr in ("speed", "accel", "decel"):
                    value = getattr(params, attr)
                    if value <= 0:
                        raise ConfigError(f"{prefix}: {label}.{attr} must be > 0")
            for attr in ("speed", "accel", "decel"):
                if getattr(ax.defaults, attr) > getattr(ax.max, attr):
                    raise ConfigError(f"{prefix}: defaults.{attr} exceeds max.{attr}")
            if ax.homing.speed <= 0 or ax.homing.accdec <= 0:
                raise ConfigError(f"{prefix}: homing speed/accdec must be > 0")
            if ax.homing.fall_step < 0:
                raise ConfigError(f"{prefix}: homing.fall_step must be >= 0")
            if ax.self_check.creep_speed <= 0 or ax.self_check.max_search_mm <= 0:
                raise ConfigError(f"{prefix}: self_check creep_speed/max_search_mm must be > 0")
            if ax.self_check.margin_mm < 0:
                raise ConfigError(f"{prefix}: self_check.margin_mm must be >= 0")
            sl = ax.soft_limits
            if sl.known and sl.min >= sl.max:  # type: ignore[operator]
                raise ConfigError(f"{prefix}: soft_limits.min must be < soft_limits.max")
        if self.sim.time_scale <= 0:
            raise ConfigError("sim.time_scale must be > 0")

    # ------------------------------------------------------------ (de)serialize
    def to_dict(self) -> dict[str, Any]:
        def convert(obj: Any) -> Any:
            if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
                return {k: convert(v) for k, v in dataclasses.asdict(obj).items()}
            if isinstance(obj, dict):
                return {str(k): convert(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [convert(v) for v in obj]
            return obj

        data = convert(self)
        data.pop("source_path", None)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ControllerConfig":
        try:
            cfg = cls(
                connection=ConnectionConfig(**data.get("connection", {})),
                axes=[_axis_from_dict(a) for a in data.get("axes", [])],
                poll_interval_s=float(data.get("poll_interval_s", 0.05)),
                sim=_sim_from_dict(data.get("sim", {})),
            )
        except TypeError as exc:
            raise ConfigError(f"invalid config structure: {exc}") from exc
        for ax in cfg.axes:
            if not ax.name:
                ax.name = AXIS_NAMES_DEFAULT[ax.index] if ax.index < MAX_AXES else f"A{ax.index}"
        cfg.validate()
        return cfg

    @classmethod
    def load(cls, path: str | Path) -> "ControllerConfig":
        path = Path(path)
        text = path.read_text(encoding="utf-8")
        if path.suffix.lower() in (".yaml", ".yml"):
            try:
                import yaml
            except ImportError as exc:
                raise ConfigError(
                    "YAML config requires PyYAML: pip install fmc-motion[yaml]"
                ) from exc
            data = yaml.safe_load(text)
        else:
            data = json.loads(text)
        cfg = cls.from_dict(data)
        cfg.source_path = str(path)
        return cfg

    def save(self, path: str | Path | None = None) -> Path:
        target = Path(path) if path is not None else (
            Path(self.source_path) if self.source_path else None
        )
        if target is None:
            raise ConfigError("no path given and config was not loaded from disk")
        data = self.to_dict()
        if target.suffix.lower() in (".yaml", ".yml"):
            try:
                import yaml
            except ImportError as exc:
                raise ConfigError(
                    "YAML config requires PyYAML: pip install fmc-motion[yaml]"
                ) from exc
            target.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        else:
            target.write_text(json.dumps(data, indent=2), encoding="utf-8")
        self.source_path = str(target)
        return target

    # ------------------------------------------------------------------ helpers
    def axis(self, index: int) -> AxisConfig:
        for ax in self.axes:
            if ax.index == index:
                return ax
        raise ConfigError(f"axis {index} is not configured")


def _axis_from_dict(data: dict[str, Any]) -> AxisConfig:
    return AxisConfig(
        index=data.get("index", 0),
        name=data.get("name", ""),
        enabled=bool(data.get("enabled", True)),
        homing=HomingConfig(**data.get("homing", {})),
        defaults=MotionParams(**data.get("defaults", {})),
        max=MotionParams(**{**dataclasses.asdict(MotionParams(300.0, 2000.0, 2000.0)),
                            **data.get("max", {})}),
        self_check=SelfCheckConfig(**data.get("self_check", {})),
        jog=JogConfig(**data.get("jog", {})),
        allow_unhomed=bool(data.get("allow_unhomed", False)),
        soft_limits=SoftLimits(**data.get("soft_limits", {})),
        move_timeout_s=data.get("move_timeout_s"),
    )


def _sim_from_dict(data: dict[str, Any]) -> SimConfig:
    axes_raw = data.get("axes", {})
    axes: dict[int, SimAxisGeometry] = {}
    if isinstance(axes_raw, dict):
        for key, geom in axes_raw.items():
            axes[int(key)] = SimAxisGeometry(**geom)
    else:  # list of {"index": n, ...}
        for geom in axes_raw:
            geom = dict(geom)
            idx = int(geom.pop("index"))
            axes[idx] = SimAxisGeometry(**geom)
    return SimConfig(time_scale=float(data.get("time_scale", 1.0)), axes=axes)

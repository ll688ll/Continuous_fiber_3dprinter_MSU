"""fmc_motion — multi-axis motion control library for FMC006GE31 controllers.

Quick start::

    from fmc_motion import create_controller

    with create_controller("machine.json", mode="sim") as ctrl:
        report = ctrl.self_check()
        ctrl.move_absolute(0, 25.0)
"""

from __future__ import annotations

from pathlib import Path
from typing import Union

from .config import (
    AxisConfig,
    ConnectionConfig,
    ControllerConfig,
    HomingConfig,
    JogConfig,
    MotionParams,
    SelfCheckConfig,
    SimAxisGeometry,
    SimConfig,
    SoftLimits,
)
from .errors import (
    ConfigError,
    ControllerConnectionError,
    DeviceBusyError,
    EStopActiveError,
    HardwareFaultError,
    LimitHitError,
    MotionError,
    MoveCancelledError,
    MoveTimeoutError,
    NotHomedError,
    SelfCheckError,
    SoftLimitError,
)
from .interface import (
    AxisStatus,
    ControllerStatus,
    MotionController,
    MoveHandle,
    MoveState,
    SelfCheckAxisReport,
    SelfCheckReport,
)
from .sim import SimMotionController
from .fmc006ge31 import FMC006GE31Controller

__version__ = "0.2.0"  # v1.1: coordinated 2-axis line moves (move_line)

ConfigLike = Union[str, Path, dict, ControllerConfig]


def create_controller(config: ConfigLike, mode: str = "real") -> MotionController:
    """Create a motion controller from a config file/dict/object.

    ``mode`` selects the backend: ``"real"`` (FMC006GE31 hardware, Windows) or
    ``"sim"`` (cross-platform simulation).
    """
    if isinstance(config, ControllerConfig):
        cfg = config
    elif isinstance(config, dict):
        cfg = ControllerConfig.from_dict(config)
    else:
        cfg = ControllerConfig.load(config)

    if mode == "sim":
        return SimMotionController(cfg)
    if mode == "real":
        return FMC006GE31Controller(cfg)
    raise ConfigError(f"unknown mode {mode!r}; expected 'real' or 'sim'")


__all__ = [
    "create_controller",
    "MotionController",
    "SimMotionController",
    "FMC006GE31Controller",
    # config
    "ControllerConfig", "AxisConfig", "ConnectionConfig", "HomingConfig",
    "MotionParams", "SelfCheckConfig", "JogConfig", "SoftLimits",
    "SimConfig", "SimAxisGeometry",
    # interface types
    "AxisStatus", "ControllerStatus", "MoveHandle", "MoveState",
    "SelfCheckReport", "SelfCheckAxisReport",
    # errors
    "MotionError", "ConfigError", "ControllerConnectionError", "NotHomedError",
    "SoftLimitError", "LimitHitError", "EStopActiveError", "DeviceBusyError",
    "HardwareFaultError", "SelfCheckError", "MoveTimeoutError",
    "MoveCancelledError",
]

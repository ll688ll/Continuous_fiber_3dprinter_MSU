"""Public interface types for fmc_motion (PRD section 5)."""

from __future__ import annotations

import json
import threading
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping, Optional

from .errors import MotionError


class MoveState(Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class MoveHandle:
    """Handle for a queued/running command (FR-MOVE-02).

    ``wait()`` blocks until the command reaches a terminal state; it raises the
    command's error if it FAILED or was CANCELLED, returns True on success, and
    returns False if the timeout elapsed first.
    """

    def __init__(self, axis: int, kind: str, description: str) -> None:
        self.axis = axis
        self.kind = kind
        self.description = description
        self.state = MoveState.QUEUED
        self.error: Optional[MotionError] = None
        self.result: Optional[Any] = None
        self._done = threading.Event()

    def wait(self, timeout: Optional[float] = None) -> bool:
        if not self._done.wait(timeout):
            return False
        if self.error is not None:
            raise self.error
        return True

    @property
    def finished(self) -> bool:
        return self._done.is_set()

    # internal transitions (called by the engine)
    def _start(self) -> None:
        self.state = MoveState.RUNNING

    def _finish_ok(self, result: Any = None) -> None:
        self.result = result
        self.state = MoveState.DONE
        self._done.set()

    def _finish_error(self, error: MotionError, cancelled: bool = False) -> None:
        self.error = error
        self.state = MoveState.CANCELLED if cancelled else MoveState.FAILED
        self._done.set()

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<MoveHandle axis={self.axis} {self.kind} {self.state.value} {self.description}>"


@dataclass(frozen=True)
class AxisStatus:
    index: int
    name: str
    position: float                 # controller step-count position, mm
    encoder_position: Optional[float]
    speed: float                    # mm/s
    homed: bool
    busy: bool
    queue_depth: int
    limit_negative: bool
    limit_positive: bool
    soft_limit_min: Optional[float]
    soft_limit_max: Optional[float]
    fault: Optional[str]


@dataclass(frozen=True)
class ControllerStatus:
    connected: bool
    estop_active: bool              # software latch OR hardware e-stop
    hardware_estop: bool
    run_state: str
    axes: Mapping[int, AxisStatus]

    @property
    def positions(self) -> dict[int, float]:
        return {i: a.position for i, a in self.axes.items()}


@dataclass
class SelfCheckAxisReport:
    axis: int
    name: str
    passed: bool
    error: Optional[str] = None
    limit_negative_before: Optional[bool] = None
    limit_positive_before: Optional[bool] = None
    span_mm: Optional[float] = None
    soft_limit_min: Optional[float] = None
    soft_limit_max: Optional[float] = None
    duration_s: Optional[float] = None


@dataclass
class SelfCheckReport:
    started_at: str
    finished_at: str = ""
    axes: list[SelfCheckAxisReport] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return bool(self.axes) and all(a.passed for a in self.axes)

    def axis(self, index: int) -> SelfCheckAxisReport:
        for a in self.axes:
            if a.axis == index:
                return a
        raise KeyError(index)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(asdict(self), indent=indent)


class MotionController(ABC):
    """Public contract of the motion control library (PRD section 5).

    Implementations: the FMC006GE31 hardware backend and the sim backend.
    All positions/speeds are mm / mm/s / mm/s^2 in the homed coordinate frame
    (0 = home switch trip point).
    """

    # lifecycle -----------------------------------------------------------
    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def disconnect(self) -> None: ...

    def __enter__(self) -> "MotionController":
        self.connect()
        return self

    def __exit__(self, *exc: object) -> None:
        self.disconnect()

    # configuration -------------------------------------------------------
    @abstractmethod
    def configure_axis(
        self,
        axis: int,
        *,
        speed: Optional[float] = None,
        accel: Optional[float] = None,
        decel: Optional[float] = None,
    ) -> None: ...

    @abstractmethod
    def get_axis_config(self, axis: int): ...

    @abstractmethod
    def save_config(self, path: Optional[str] = None): ...

    # homing --------------------------------------------------------------
    @abstractmethod
    def home(self, axis: int, wait: bool = True, timeout: Optional[float] = None) -> MoveHandle: ...

    @abstractmethod
    def home_all(self, order: Optional[list[int]] = None, timeout: Optional[float] = None) -> None: ...

    @abstractmethod
    def self_check(self, axes: Optional[list[int]] = None, continue_on_error: bool = True) -> SelfCheckReport: ...

    @abstractmethod
    def is_homed(self, axis: int) -> bool: ...

    # motion --------------------------------------------------------------
    @abstractmethod
    def move_absolute(
        self, axis: int, position: float, *,
        speed: Optional[float] = None, accel: Optional[float] = None, decel: Optional[float] = None,
        wait: bool = True, timeout: Optional[float] = None,
    ) -> MoveHandle: ...

    @abstractmethod
    def move_relative(
        self, axis: int, delta: float, *,
        speed: Optional[float] = None, accel: Optional[float] = None, decel: Optional[float] = None,
        wait: bool = True, timeout: Optional[float] = None,
    ) -> MoveHandle: ...

    @abstractmethod
    def jog(self, axis: int, direction: int, speed: Optional[float] = None) -> MoveHandle: ...

    @abstractmethod
    def jog_stop(self, axis: int) -> None: ...

    @abstractmethod
    def wait_for(self, axis: int, timeout: Optional[float] = None) -> bool: ...

    # queue ---------------------------------------------------------------
    @abstractmethod
    def get_queue(self, axis: int) -> list[str]: ...

    @abstractmethod
    def clear_queue(self, axis: Optional[int] = None) -> None: ...

    # safety --------------------------------------------------------------
    @abstractmethod
    def stop(self, axis: Optional[int] = None, mode: str = "controlled") -> None: ...

    @abstractmethod
    def estop(self) -> None: ...

    @abstractmethod
    def clear_estop(self) -> None: ...

    @abstractmethod
    def clear_fault(self, axis: int) -> None: ...

    @abstractmethod
    def set_soft_limits_enabled(self, enabled: bool) -> None: ...

    # status --------------------------------------------------------------
    @abstractmethod
    def get_status(self) -> ControllerStatus: ...

    @abstractmethod
    def get_position(self, axis: int) -> float: ...

    @abstractmethod
    def start_polling(self, rate_hz: float, callback: Callable[[ControllerStatus], None]) -> None: ...

    @abstractmethod
    def stop_polling(self) -> None: ...

    @abstractmethod
    def capabilities(self) -> dict[str, Any]: ...

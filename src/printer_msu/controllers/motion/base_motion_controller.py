from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Optional


class StopMode(str, Enum):
    CONTROLLED = "controlled"
    IMMEDIATE = "immediate"


@dataclass(frozen=True)
class NormalizedError:
    code: str
    message: str
    raw: object | None = None


@dataclass(frozen=True)
class MotionStatus:
    positions: Mapping[str, float]
    speeds: Mapping[str, float]
    emergency_active: bool
    run_state: str
    last_error: Optional[NormalizedError] = None


class BaseMotionController(ABC):
    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def disconnect(self) -> None: ...

    @abstractmethod
    def get_status(self) -> MotionStatus: ...

    @abstractmethod
    def home(self, axes: Optional[list[str]] = None) -> None: ...

    @abstractmethod
    def move_absolute(
        self,
        axes_positions: Mapping[str, float],
        feed: float,
        accel: float | None = None,
        decel: float | None = None,
    ) -> None: ...

    @abstractmethod
    def move_relative(
        self,
        axes_deltas: Mapping[str, float],
        feed: float,
        accel: float | None = None,
        decel: float | None = None,
    ) -> None: ...

    @abstractmethod
    def stop(self, mode: StopMode = StopMode.CONTROLLED) -> None: ...

    @abstractmethod
    def estop(self) -> None: ...



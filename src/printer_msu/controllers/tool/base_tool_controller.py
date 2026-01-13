from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Mapping, Optional

from ..motion.base_motion_controller import NormalizedError


@dataclass(frozen=True)
class TemperatureStatus:
    current_c: float
    target_c: float
    heater_on: bool


@dataclass(frozen=True)
class ToolStatus:
    temperatures: Mapping[int, TemperatureStatus]
    fans: Mapping[int, float]
    last_error: Optional[NormalizedError] = None


class BaseToolController(ABC):
    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def disconnect(self) -> None: ...

    @abstractmethod
    def get_status(self) -> ToolStatus: ...

    @abstractmethod
    def set_target_temperature(self, channel: int, target_c: float) -> None: ...

    @abstractmethod
    def heater_off(self, channel: int) -> None: ...

    @abstractmethod
    def set_fan_speed(self, channel: int, value: float) -> None: ...

    @abstractmethod
    def extrude(self, channel: int, distance_mm: float, feed_mm_s: float) -> None: ...

    @abstractmethod
    def emergency_shutdown(self) -> None: ...



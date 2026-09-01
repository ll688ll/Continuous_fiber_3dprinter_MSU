"""The motion HAL every backend implements (printer PRD v2, FR-DUET-01).

This is the WIDE contract the runner, preflight, and UI actually use — it was
implicit in v1 (only the narrow half lived on the ABC while callers relied on
adapter extras); v2 promotes the whole surface so a new backend cannot half-
implement it by accident.

Backends: `FmcMotionAdapter` (fmc_motion library, sim + FMC hardware) and
`DuetMotionAdapter` (Duet 3 via the RRF HTTP API).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Optional, Protocol


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


class MoveHandle(Protocol):
    """Handle returned by ``move_async``; ``wait()`` blocks until the move
    reaches a terminal state and raises its error on failure."""

    def wait(self, timeout: Optional[float] = None) -> bool: ...

    @property
    def finished(self) -> bool: ...


class BaseMotionController(ABC):
    # ------------------------------------------------------------- lifecycle
    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def disconnect(self) -> None: ...

    # ---------------------------------------------------------------- status
    @abstractmethod
    def get_status(self) -> MotionStatus: ...

    @abstractmethod
    def full_status(self) -> dict:
        """Rich snapshot in one round trip:
        ``{axes: {name: {position, speed, homed, busy, fault}},
        estop: bool, connected: bool}`` (the UI poller's contract)."""

    @abstractmethod
    def homed_axes(self) -> set[str]:
        """Logical names of currently homed axes (preflight)."""

    @abstractmethod
    def max_speeds(self) -> dict[str, float]:
        """Configured max speed (mm/s) per logical axis (feed clamping)."""

    @abstractmethod
    def soft_limits(self) -> dict[str, tuple[float, float] | None]:
        """Per-axis working range, ``None`` while unknown (envelope check)."""

    @abstractmethod
    def capabilities(self) -> dict:
        """Backend capability flags. Keys the core relies on:
        ``axis_names`` (list[str]); ``execution`` ("moves" = per-move
        dispatch via move_async, "gcode_file" = whole-program execution,
        see the Duet strategies); ``interpolation`` (bool)."""

    # ---------------------------------------------------------------- motion
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
    def move_async(self, axes_positions: Mapping[str, float], feed: float,
                   accel: float | None = None,
                   decel: float | None = None) -> MoveHandle:
        """Dispatch an absolute move without waiting (streamed printing).
        Backends whose ``capabilities()["execution"]`` is not "moves" may
        raise ``NotImplementedError`` — the runner routes around them."""

    # ---------------------------------------------------------------- safety
    @abstractmethod
    def stop(self, mode: StopMode = StopMode.CONTROLLED) -> None: ...

    @abstractmethod
    def estop(self) -> None: ...

    @abstractmethod
    def clear_estop(self) -> None: ...

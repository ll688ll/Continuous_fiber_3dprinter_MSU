"""Adapter: fmc_motion library -> printer_msu's BaseMotionController contract.

The printer core speaks the mapping-style HAL (`move_absolute({"X": 10.0}, feed)`
with logical axis names); the fmc_motion library speaks per-axis indices. This
adapter bridges the two so the printer can use the hardware-proven library
without changes to either side.

Requires `fmc-motion` to be installed (dev: ``pip install -e libs/fmc-motion``).

v1 semantics (independent axes, per the motion-library PRD):
- A multi-axis move dispatches each axis concurrently and waits for all —
  axes start/finish independently; this is NOT interpolated line motion.
- ``feed`` applies to each axis individually.
"""

from __future__ import annotations

from typing import Mapping, Optional

from .base_motion_controller import (
    BaseMotionController,
    MotionStatus,
    NormalizedError,
    StopMode,
)


class FmcMotionAdapter(BaseMotionController):
    """Wraps a ``fmc_motion.MotionController`` behind the printer HAL."""

    def __init__(self, controller, axis_map: Optional[Mapping[str, int]] = None) -> None:
        """``controller``: an fmc_motion MotionController (real or sim).
        ``axis_map``: logical axis name -> axis index; defaults to the names
        configured in the library's axis configs."""
        self._ctrl = controller
        if axis_map is None:
            axis_map = {
                controller.get_axis_config(i).name: i
                for i in controller.capabilities()["axes"]
            }
        self._name_to_index = dict(axis_map)
        self._index_to_name = {i: n for n, i in self._name_to_index.items()}

    # ------------------------------------------------------------- lifecycle
    def connect(self) -> None:
        self._ctrl.connect()

    def disconnect(self) -> None:
        self._ctrl.disconnect()

    # ---------------------------------------------------------------- status
    def get_status(self) -> MotionStatus:
        status = self._ctrl.get_status()
        positions: dict[str, float] = {}
        speeds: dict[str, float] = {}
        last_error: Optional[NormalizedError] = None
        for idx, ax in status.axes.items():
            name = self._index_to_name.get(idx, str(idx))
            positions[name] = ax.position
            speeds[name] = ax.speed
            if ax.fault and last_error is None:
                last_error = NormalizedError(code="AXIS_FAULT",
                                             message=f"{name}: {ax.fault}")
        busy = any(ax.busy for ax in status.axes.values())
        return MotionStatus(
            positions=positions,
            speeds=speeds,
            emergency_active=status.estop_active,
            run_state="running" if busy else "idle",
            last_error=last_error,
        )

    # ---------------------------------------------------------------- motion
    def home(self, axes: Optional[list[str]] = None) -> None:
        indices = ([self._index_of(a) for a in axes] if axes is not None
                   else sorted(self._index_to_name))
        for idx in indices:
            self._ctrl.home(idx, wait=True)

    def move_absolute(
        self,
        axes_positions: Mapping[str, float],
        feed: float,
        accel: float | None = None,
        decel: float | None = None,
    ) -> None:
        self._move(axes_positions, feed, accel, decel, relative=False)

    def move_relative(
        self,
        axes_deltas: Mapping[str, float],
        feed: float,
        accel: float | None = None,
        decel: float | None = None,
    ) -> None:
        self._move(axes_deltas, feed, accel, decel, relative=True)

    def _move(self, values: Mapping[str, float], feed: float,
              accel: float | None, decel: float | None, *, relative: bool) -> None:
        handles = []
        for name, value in values.items():
            idx = self._index_of(name)
            fn = self._ctrl.move_relative if relative else self._ctrl.move_absolute
            handles.append(fn(idx, value, speed=feed, accel=accel, decel=decel,
                              wait=False))
        for handle in handles:
            handle.wait()

    def stop(self, mode: StopMode = StopMode.CONTROLLED) -> None:
        self._ctrl.stop(mode="immediate" if mode is StopMode.IMMEDIATE
                        else "controlled")

    def estop(self) -> None:
        self._ctrl.estop()

    # ----------------------------------------------------------------- extra
    def capabilities(self) -> dict:
        caps = dict(self._ctrl.capabilities())
        caps["axis_names"] = sorted(self._name_to_index)
        return caps

    def _index_of(self, name: str) -> int:
        try:
            return self._name_to_index[name]
        except KeyError:
            raise KeyError(
                f"unknown axis {name!r}; configured: {sorted(self._name_to_index)}"
            ) from None

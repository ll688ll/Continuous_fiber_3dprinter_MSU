"""Adapter: fmc_motion library -> printer_msu's BaseMotionController contract.

The printer core speaks the mapping-style HAL (`move_absolute({"X": 10.0}, feed)`
with logical axis names); the fmc_motion library speaks per-axis indices. This
adapter bridges the two so the printer can use the hardware-proven library
without changes to either side.

Requires `fmc-motion` to be installed (dev: ``pip install -e libs/fmc-motion``).

Motion semantics (library v1.1):
- An **absolute move on exactly 2 axes** runs as one interpolated straight
  line (`move_line`, vendor Line_2Axis); ``feed`` is the composite path feed.
- Any other multi-axis move (relative, or 3+ axes) dispatches each axis
  concurrently and waits for all — independent axes, NOT a straight line.
  The printer core only ever sends 1- and 2-axis absolute moves (the
  interpreter splits Z from XY), so prints always get true lines.
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
        if not relative and len(values) == 2 and self._ctrl.capabilities().get(
                "interpolation"):
            targets = {self._index_of(n): v for n, v in values.items()}
            kwargs = {}
            if accel is not None:
                kwargs["accel"] = accel
            if decel is not None:
                kwargs["decel"] = decel
            self._ctrl.move_line(targets, speed=feed, wait=True, **kwargs)
            return
        handles = []
        for name, value in values.items():
            idx = self._index_of(name)
            fn = self._ctrl.move_relative if relative else self._ctrl.move_absolute
            handles.append(fn(idx, value, speed=feed, accel=accel, decel=decel,
                              wait=False))
        for handle in handles:
            handle.wait()

    def move_async(self, axes_positions: Mapping[str, float], feed: float,
                   accel: float | None = None, decel: float | None = None):
        """Dispatch an absolute move WITHOUT waiting; returns the library
        MoveHandle (streamed printing). The library's per-axis queues order
        successive calls; ``handle.wait()`` raises the move's error.

        1 axis -> point-to-point; 2 axes -> coordinated line. The printer
        core never produces 3+-axis moves (the interpreter splits Z from XY).
        """
        kwargs = {}
        if accel is not None:
            kwargs["accel"] = accel
        if decel is not None:
            kwargs["decel"] = decel
        if len(axes_positions) == 2 and self._ctrl.capabilities().get(
                "interpolation"):
            targets = {self._index_of(n): v for n, v in axes_positions.items()}
            return self._ctrl.move_line(targets, speed=feed, wait=False, **kwargs)
        if len(axes_positions) == 1:
            (name, value), = axes_positions.items()
            return self._ctrl.move_absolute(self._index_of(name), value,
                                            speed=feed, wait=False, **kwargs)
        raise ValueError("move_async supports 1- or 2-axis absolute moves")

    def stop(self, mode: StopMode = StopMode.CONTROLLED) -> None:
        self._ctrl.stop(mode="immediate" if mode is StopMode.IMMEDIATE
                        else "controlled")

    def estop(self) -> None:
        self._ctrl.estop()

    def clear_estop(self) -> None:
        """Release the software e-stop latch (fails while hardware e-stop
        input is still active)."""
        self._ctrl.clear_estop()

    # ----------------------------------------------------------------- extra
    def capabilities(self) -> dict:
        caps = dict(self._ctrl.capabilities())
        caps["axis_names"] = sorted(self._name_to_index)
        return caps

    def homed_axes(self) -> set[str]:
        """Logical names of axes that are homed (preflight, FR-PRE-01)."""
        status = self._ctrl.get_status()
        return {self._index_to_name[i] for i, ax in status.axes.items()
                if ax.homed and i in self._index_to_name}

    def max_speeds(self) -> dict[str, float]:
        """Configured max speed (mm/s) per logical axis (feed clamping)."""
        return {name: self._ctrl.get_axis_config(idx).max.speed
                for name, idx in self._name_to_index.items()}

    def soft_limits(self) -> dict[str, tuple[float, float] | None]:
        """Current per-axis soft limits (None while undiscovered) for
        preflight envelope checking (FR-PRE-03)."""
        status = self._ctrl.get_status()
        out: dict[str, tuple[float, float] | None] = {}
        for idx, ax in status.axes.items():
            name = self._index_to_name.get(idx)
            if name is None:
                continue
            known = ax.soft_limit_min is not None and ax.soft_limit_max is not None
            out[name] = ((ax.soft_limit_min, ax.soft_limit_max) if known
                         else None)
        return out

    def full_status(self) -> dict:
        """Rich per-axis snapshot for status displays, one controller
        round-trip: {axes: {name: {position, speed, homed, busy, fault}},
        estop, connected}."""
        status = self._ctrl.get_status()
        axes = {}
        for idx, ax in status.axes.items():
            name = self._index_to_name.get(idx)
            if name is None:
                continue
            axes[name] = {"position": ax.position, "speed": ax.speed,
                          "homed": ax.homed, "busy": ax.busy,
                          "fault": ax.fault}
        return {"axes": axes, "estop": status.estop_active,
                "connected": status.connected}

    def _index_of(self, name: str) -> int:
        try:
            return self._name_to_index[name]
        except KeyError:
            raise KeyError(
                f"unknown axis {name!r}; configured: {sorted(self._name_to_index)}"
            ) from None

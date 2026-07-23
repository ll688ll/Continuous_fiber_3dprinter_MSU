"""Simulation backend (PRD FR-SIM-01..06).

Models each axis as a linear actuator with:

* trapezoidal (or triangular) velocity profiles driven by the commanded
  speed/accel/decel, so positions/speeds/busy flags evolve realistically in
  time (FR-SIM-02);
* two virtual limit switches at configurable trip points, with a small
  physical overtravel past each trip point (FR-SIM-03) — motion clamps at the
  hard end, the switch bit stays asserted while the axis is at/past the trip
  point;
* vendor-style homing: drive to the configured switch, back off ``fall_step``,
  and re-zero the reported coordinate frame at the switch trip point;
* fault injection hooks (FR-SIM-04) and a command history (FR-SIM-05);
* an optional ``time_scale`` so long sequences run fast in CI (FR-SIM-06).

Internal vs reported frame: the model tracks a fixed *internal* coordinate
(negative switch trip point = 0, positive trip point = travel). The controller
*reports* positions relative to its power-on zero, exactly like the real
hardware; homing re-anchors the reported frame to the switch.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from .config import ControllerConfig, HOMING_NEGATIVE
from .errors import (
    ControllerConnectionError,
    DeviceBusyError,
    HardwareFaultError,
    describe_vendor_code,
)
from ._engine import EngineController, LLSnapshot


# ------------------------------------------------------------------ kinematics
@dataclass
class _Segment:
    duration: float
    v0: float   # signed velocity at segment start
    a: float    # signed acceleration


class _Profile:
    """Piecewise constant-acceleration motion profile."""

    def __init__(self, t0: float, p0: float, segments: list[_Segment],
                 end_pos: float) -> None:
        self.t0 = t0
        self.p0 = p0
        self.segments = segments
        self.total = sum(s.duration for s in segments)
        self.end_pos = end_pos

    @classmethod
    def point_to_point(cls, t0: float, p0: float, target: float, v: float,
                       a: float, d: float) -> "_Profile":
        dist = abs(target - p0)
        if dist <= 1e-12:
            return cls(t0, p0, [], p0)
        s = 1.0 if target > p0 else -1.0
        d_acc = v * v / (2 * a)
        d_dec = v * v / (2 * d)
        if d_acc + d_dec <= dist:
            segs = [
                _Segment(v / a, 0.0, s * a),
                _Segment((dist - d_acc - d_dec) / v, s * v, 0.0),
                _Segment(v / d, s * v, -s * d),
            ]
        else:
            vp = math.sqrt(2 * dist * a * d / (a + d))
            segs = [_Segment(vp / a, 0.0, s * a), _Segment(vp / d, s * vp, -s * d)]
        return cls(t0, p0, segs, target)

    @classmethod
    def decel_to_stop(cls, t0: float, p0: float, v_signed: float, d: float) -> "_Profile":
        if abs(v_signed) <= 1e-12:
            return cls(t0, p0, [], p0)
        s = 1.0 if v_signed > 0 else -1.0
        stop_dist = v_signed * v_signed / (2 * d)
        return cls(t0, p0, [_Segment(abs(v_signed) / d, v_signed, -s * d)],
                   p0 + s * stop_dist)

    def state(self, t: float) -> tuple[float, float]:
        """Return (position, signed velocity) at absolute sim-time ``t``."""
        dt = t - self.t0
        if dt >= self.total:
            return self.end_pos, 0.0
        pos = self.p0
        for seg in self.segments:
            if dt <= seg.duration:
                return (pos + seg.v0 * dt + 0.5 * seg.a * dt * dt,
                        seg.v0 + seg.a * dt)
            pos += seg.v0 * seg.duration + 0.5 * seg.a * seg.duration ** 2
            dt -= seg.duration
        return self.end_pos, 0.0

    def done(self, t: float) -> bool:
        return (t - self.t0) >= self.total


# ------------------------------------------------------------------- sim axis
class _SimAxis:
    """One simulated actuator (internal frame: neg trip = 0, pos trip = travel)."""

    def __init__(self, index: int, travel: float, start_pos: Optional[float],
                 overtravel: float) -> None:
        self.index = index
        self.travel = travel
        self.overtravel = overtravel
        self.hard_min = -overtravel
        self.hard_max = travel + overtravel
        internal_start = start_pos if start_pos is not None else travel / 2.0
        self.static_pos = float(internal_start)
        # reported = internal - offset; power-on zero at the start position
        self.offset = float(internal_start)
        self.profile: Optional[_Profile] = None
        self.home_pending: Optional[tuple[float, float]] = None  # (switch_pos, final_pos)
        self.stalled = False

    # -- state -------------------------------------------------------------
    def current(self, t: float) -> tuple[float, float]:
        """(internal position clamped to hard ends, signed velocity)."""
        self._finalize_if_done(t)
        if self.profile is None:
            return self.static_pos, 0.0
        pos, vel = self.profile.state(t)
        if pos <= self.hard_min:
            return self.hard_min, vel
        if pos >= self.hard_max:
            return self.hard_max, vel
        return pos, vel

    def is_stopped(self, t: float) -> bool:
        # a stall means a *started* move never reports completion; an idle
        # stalled axis still accepts commands
        if self.profile is not None and self.stalled:
            return False
        self._finalize_if_done(t)
        return self.profile is None

    def _finalize_if_done(self, t: float) -> None:
        if self.profile is not None and not self.stalled and self.profile.done(t):
            end = min(max(self.profile.end_pos, self.hard_min), self.hard_max)
            self.static_pos = end
            self.profile = None
            if self.home_pending is not None:
                switch_pos, final_pos = self.home_pending
                self.offset = switch_pos      # re-zero reported frame at switch
                self.static_pos = final_pos
                self.home_pending = None

    # -- commands ----------------------------------------------------------
    def start_move(self, t: float, target_internal: float, v: float, a: float,
                   d: float) -> None:
        pos, _ = self.current(t)
        self.profile = _Profile.point_to_point(t, pos, target_internal, v, a, d)

    def start_home(self, t: float, speed: float, accdec: float, fall_step: float,
                   direction_positive: bool) -> None:
        pos, _ = self.current(t)
        switch = self.travel if direction_positive else 0.0
        fall_dir = -1.0 if direction_positive else 1.0
        final = switch + fall_dir * fall_step
        # phase 1: to the switch; phase 2: fall back off it
        p1 = _Profile.point_to_point(t, pos, switch, speed, accdec, accdec)
        p2 = _Profile.point_to_point(t + p1.total, switch, final, speed, accdec, accdec)
        self.profile = _Profile(t, pos, p1.segments + p2.segments, final)
        self.home_pending = (switch, final)

    def stop(self, t: float, immediate: bool, decel: float) -> None:
        self.stalled = False
        pos, vel = self.current(t)
        self.home_pending = None
        if self.profile is None:
            return
        if immediate or abs(vel) <= 1e-12:
            self.profile = None
            self.static_pos = pos
        else:
            self.profile = _Profile.decel_to_stop(t, pos, vel, decel)

    # -- switches ----------------------------------------------------------
    def limit_states(self, t: float) -> tuple[bool, bool]:
        pos, _ = self.current(t)
        return pos <= 0.0 + 1e-9, pos >= self.travel - 1e-9

    def reported(self, internal: float) -> float:
        return internal - self.offset

    def to_internal(self, reported: float) -> float:
        return reported + self.offset


# ------------------------------------------------------------- sim controller
class SimMotionController(EngineController):
    """Drop-in simulated controller (FR-SIM-01)."""

    def __init__(self, config: ControllerConfig) -> None:
        super().__init__(config)
        self._time_scale = config.sim.time_scale
        self._epoch: Optional[float] = None
        self._models: dict[int, _SimAxis] = {}
        self._model_lock = threading.RLock()
        for idx in self._axes:
            geom = config.sim.geometry(idx)
            self._models[idx] = _SimAxis(idx, geom.travel_mm, geom.start_pos_mm,
                                         geom.overtravel_mm)
        # fault injection state (FR-SIM-04)
        self._inject_connect_fail: Optional[int] = None
        self._inject_vendor_errors: list[int] = []
        self._inject_limit_stuck: dict[tuple[int, str], bool] = {}
        self._inject_hw_estop = False
        #: command history for test assertions (FR-SIM-05)
        self.history: list[dict[str, Any]] = []

    @property
    def is_sim(self) -> bool:
        return True

    # ------------------------------------------------------------- sim clock
    def _now(self) -> float:
        if self._epoch is None:
            return 0.0
        return (time.monotonic() - self._epoch) * self._time_scale

    # ------------------------------------------------------ fault injection
    def inject_connect_failure(self, vendor_code: int = -1) -> None:
        self._inject_connect_fail = vendor_code

    def inject_vendor_error(self, vendor_code: int, count: int = 1) -> None:
        self._inject_vendor_errors.extend([vendor_code] * count)

    def inject_limit_stuck(self, axis: int, side: str, active: bool = True) -> None:
        """Force a limit-switch bit to read ``active`` regardless of position."""
        assert side in ("negative", "positive")
        self._inject_limit_stuck[(axis, side)] = active

    def clear_limit_stuck(self, axis: int, side: str) -> None:
        self._inject_limit_stuck.pop((axis, side), None)

    def inject_hardware_estop(self, active: bool) -> None:
        self._inject_hw_estop = active

    def inject_move_stall(self, axis: int, stalled: bool = True) -> None:
        with self._model_lock:
            self._models[axis].stalled = stalled

    # ------------------------------------------------------------ ll interface
    def _record(self, op: str, **params: Any) -> None:
        self.history.append({"op": op, "t": round(self._now(), 6), **params})

    def _consume_injected_error(self) -> None:
        if self._inject_vendor_errors:
            code = self._inject_vendor_errors.pop(0)
            raise HardwareFaultError(
                f"injected vendor error {code} ({describe_vendor_code(code)})",
                vendor_code=code,
            )

    def _ll_connect(self) -> None:
        if self._inject_connect_fail is not None:
            code = self._inject_connect_fail
            self._inject_connect_fail = None
            raise ControllerConnectionError(
                f"injected connect failure ({describe_vendor_code(code)})",
                vendor_code=code,
            )
        self._epoch = time.monotonic()
        self._record("connect")

    def _ll_disconnect(self) -> None:
        self._record("disconnect")

    def _ll_move(self, axis: int, value: float, speed: float, accel: float,
                 decel: float, relative: bool) -> None:
        self._consume_injected_error()
        with self._model_lock:
            model = self._models[axis]
            t = self._now()
            if not model.is_stopped(t):
                raise DeviceBusyError(f"axis {axis} busy (vendor -10)")
            pos, _ = model.current(t)
            target_internal = pos + value if relative else model.to_internal(value)
            model.start_move(t, target_internal, speed, accel, decel)
        self._record("move", axis=axis, value=value, relative=relative,
                     speed=speed, accel=accel, decel=decel)

    def _ll_home(self, axis: int, speed: float, accdec: float, fall_step: float,
                 direction_positive: bool) -> None:
        self._consume_injected_error()
        with self._model_lock:
            model = self._models[axis]
            t = self._now()
            if not model.is_stopped(t):
                raise DeviceBusyError(f"axis {axis} busy (vendor -10)")
            model.start_home(t, speed, accdec, fall_step, direction_positive)
        self._record("home", axis=axis, speed=speed, fall_step=fall_step,
                     direction_positive=direction_positive)

    def _ll_is_stopped(self, axis: int) -> bool:
        with self._model_lock:
            return self._models[axis].is_stopped(self._now())

    def _ll_stop(self, axis: int, immediate: bool) -> None:
        with self._model_lock:
            decel = self._axes[axis].cfg.defaults.decel if axis in self._axes else 1000.0
            self._models[axis].stop(self._now(), immediate, decel)
        self._record("stop", axis=axis, immediate=immediate)

    def _ll_snapshot(self) -> LLSnapshot:
        with self._model_lock:
            t = self._now()
            positions: dict[int, float] = {}
            encoders: dict[int, Optional[float]] = {}
            speeds: dict[int, float] = {}
            lim_n: dict[int, bool] = {}
            lim_p: dict[int, bool] = {}
            for idx, model in self._models.items():
                pos, vel = model.current(t)
                positions[idx] = model.reported(pos)
                encoders[idx] = model.reported(pos)
                speeds[idx] = abs(vel)
                neg, pos_lim = model.limit_states(t)
                neg = self._inject_limit_stuck.get((idx, "negative"), neg)
                pos_lim = self._inject_limit_stuck.get((idx, "positive"), pos_lim)
                lim_n[idx] = neg
                lim_p[idx] = pos_lim
            running = any(not m.is_stopped(t) for m in self._models.values())
            return LLSnapshot(
                positions=positions,
                encoder_positions=encoders,
                speeds=speeds,
                limit_negative=lim_n,
                limit_positive=lim_p,
                hardware_estop=self._inject_hw_estop,
                run_state="running" if running else "idle",
            )

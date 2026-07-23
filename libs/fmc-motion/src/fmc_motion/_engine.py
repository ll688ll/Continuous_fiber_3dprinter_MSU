"""Shared controller engine: queuing, safety, homing/self-check orchestration.

The engine implements the whole public ``MotionController`` API on top of a
small set of abstract low-level primitives (``_ll_*``). The sim backend and the
FMC006GE31 hardware backend implement only those primitives, so queue policy,
soft limits, e-stop latching, and self-check behave identically in both modes
(PRD FR-SIM-02).

Threading model
---------------
* One dispatcher (worker) thread per enabled axis consumes that axis's FIFO
  command queue (FR-MOVE-03).
* All ``_ll_*`` calls are serialized through a single dispatch lock per
  controller (NFR-01). ``stop``/``estop`` bypass the queue and call ``_ll_stop``
  directly (NFR-02); axis workers observe the abort flag and finish the
  in-flight command as CANCELLED.
* Status snapshots are cached briefly so six workers polling do not hammer the
  link.
"""

from __future__ import annotations

import datetime as _dt
import logging
import queue as queue_mod
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .config import (
    AxisConfig,
    ControllerConfig,
    HOMING_NEGATIVE,
)
from .errors import (
    ControllerConnectionError,
    EStopActiveError,
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
    SelfCheckAxisReport,
    SelfCheckReport,
)

logger = logging.getLogger("fmc_motion")


# --------------------------------------------------------------------------- ll
@dataclass
class LLSnapshot:
    """Normalized low-level status of all axes (one Get_Machine_Status call)."""

    positions: dict[int, float]
    encoder_positions: dict[int, Optional[float]]
    speeds: dict[int, float]
    limit_negative: dict[int, bool]
    limit_positive: dict[int, bool]
    hardware_estop: bool
    run_state: str = "idle"


# ----------------------------------------------------------------------- cmds
_KIND_MOVE = "move"
_KIND_HOME = "home"
_KIND_CREEP = "creep"  # self-check range search: move until far limit trips


@dataclass
class _Cmd:
    kind: str
    handle: MoveHandle
    target: float = 0.0          # absolute target (move) / signed max distance (creep)
    relative: bool = False
    speed: float = 0.0
    accel: float = 0.0
    decel: float = 0.0
    direction: int = 0           # creep: +1 toward positive limit, -1 toward negative


@dataclass
class _AxisRuntime:
    cfg: AxisConfig
    queue: "queue_mod.Queue[_Cmd]" = field(default_factory=queue_mod.Queue)
    worker: Optional[threading.Thread] = None
    homed: bool = False
    fault: Optional[str] = None
    abort: threading.Event = field(default_factory=threading.Event)
    predicted_end: Optional[float] = None  # end position after all queued cmds
    # runtime soft limits (seeded from config, updated by self_check)
    soft_min: Optional[float] = None
    soft_max: Optional[float] = None


class EngineController(MotionController):
    """Concrete engine; subclasses provide the ``_ll_*`` primitives."""

    def __init__(self, config: ControllerConfig) -> None:
        config.validate()
        self._cfg = config
        self._axes: dict[int, _AxisRuntime] = {}
        for ax_cfg in config.axes:
            if not ax_cfg.enabled:
                continue
            rt = _AxisRuntime(cfg=ax_cfg)
            rt.soft_min = ax_cfg.soft_limits.min
            rt.soft_max = ax_cfg.soft_limits.max
            self._axes[ax_cfg.index] = rt

        self._ll_lock = threading.RLock()      # serializes all _ll_* calls
        self._state_lock = threading.RLock()   # engine bookkeeping
        self._connected = False
        self._shutdown = threading.Event()
        self._estop_latched = False
        self._soft_limits_enabled = True
        self._poll_thread: Optional[threading.Thread] = None
        self._poll_stop = threading.Event()
        self._snap_cache: Optional[LLSnapshot] = None
        self._snap_time = 0.0

    # ------------------------------------------------------------ ll interface
    def _ll_connect(self) -> None:
        raise NotImplementedError

    def _ll_disconnect(self) -> None:
        raise NotImplementedError

    def _ll_move(self, axis: int, value: float, speed: float, accel: float,
                 decel: float, relative: bool) -> None:
        raise NotImplementedError

    def _ll_home(self, axis: int, speed: float, accdec: float, fall_step: float,
                 direction_positive: bool) -> None:
        raise NotImplementedError

    def _ll_is_stopped(self, axis: int) -> bool:
        raise NotImplementedError

    def _ll_stop(self, axis: int, immediate: bool) -> None:
        raise NotImplementedError

    def _ll_snapshot(self) -> LLSnapshot:
        raise NotImplementedError

    # ---------------------------------------------------------------- lifecycle
    def connect(self) -> None:
        with self._state_lock:
            if self._connected:
                return
            logger.info("connect: device_id=%s ip=%s port=%s",
                        self._cfg.connection.device_id, self._cfg.connection.ip,
                        self._cfg.connection.port)
            with self._ll_lock:
                self._ll_connect()
            self._shutdown.clear()
            self._connected = True
            for rt in self._axes.values():
                rt.worker = threading.Thread(
                    target=self._worker_loop, args=(rt,),
                    name=f"fmc-axis-{rt.cfg.index}", daemon=True,
                )
                rt.worker.start()
            logger.info("connect: ok (%d axes)", len(self._axes))

    def disconnect(self) -> None:
        with self._state_lock:
            if not self._connected:
                return
            logger.info("disconnect")
            self.stop_polling()
            self._shutdown.set()
        for rt in self._axes.values():
            self._flush_axis(rt, MoveCancelledError("controller disconnecting"))
            if rt.worker is not None:
                rt.worker.join(timeout=5.0)
                rt.worker = None
        with self._state_lock:
            with self._ll_lock:
                self._ll_disconnect()
            self._connected = False

    # ------------------------------------------------------------ configuration
    def configure_axis(self, axis: int, *, speed: Optional[float] = None,
                       accel: Optional[float] = None,
                       decel: Optional[float] = None) -> None:
        rt = self._axis(axis)
        with self._state_lock:
            for attr, value in (("speed", speed), ("accel", accel), ("decel", decel)):
                if value is None:
                    continue
                maximum = getattr(rt.cfg.max, attr)
                if value <= 0 or value > maximum:
                    raise SoftLimitError(
                        f"axis {axis}: {attr}={value} outside (0, {maximum}]"
                    )
                setattr(rt.cfg.defaults, attr, value)
            logger.info("configure_axis %s: %s", axis, rt.cfg.defaults)

    def get_axis_config(self, axis: int) -> AxisConfig:
        return self._axis(axis).cfg

    def save_config(self, path: Optional[str] = None):
        # write runtime-discovered soft limits back into the config object
        with self._state_lock:
            for rt in self._axes.values():
                rt.cfg.soft_limits.min = rt.soft_min
                rt.cfg.soft_limits.max = rt.soft_max
            return self._cfg.save(path)

    # ---------------------------------------------------------------- homing
    def home(self, axis: int, wait: bool = True,
             timeout: Optional[float] = None) -> MoveHandle:
        rt = self._axis(axis)
        cmd = _Cmd(
            kind=_KIND_HOME,
            handle=MoveHandle(axis, _KIND_HOME, f"home dir={rt.cfg.homing.direction}"),
        )
        self._enqueue(rt, cmd, validate_target=False)
        if wait:
            cmd.handle.wait(timeout)
        return cmd.handle

    def home_all(self, order: Optional[list[int]] = None,
                 timeout: Optional[float] = None) -> None:
        for axis in (order if order is not None else sorted(self._axes)):
            self.home(axis, wait=True, timeout=timeout)

    def is_homed(self, axis: int) -> bool:
        return self._axis(axis).homed

    # ------------------------------------------------------------- self-check
    def self_check(self, axes: Optional[list[int]] = None,
                   continue_on_error: bool = True) -> SelfCheckReport:
        report = SelfCheckReport(started_at=_now_iso())
        targets = axes if axes is not None else sorted(self._axes)
        for axis in targets:
            axis_report = self._self_check_axis(axis)
            report.axes.append(axis_report)
            logger.info("self_check axis %s: %s", axis,
                        "PASS" if axis_report.passed else f"FAIL ({axis_report.error})")
            if not axis_report.passed and not continue_on_error:
                break
        report.finished_at = _now_iso()
        return report

    def _self_check_axis(self, axis: int) -> SelfCheckAxisReport:
        rt = self._axis(axis)
        sc = rt.cfg.self_check
        rep = SelfCheckAxisReport(axis=axis, name=rt.cfg.name, passed=False)
        t0 = time.monotonic()
        try:
            with self._state_lock:
                if not rt.queue.empty():
                    raise SelfCheckError(f"axis {axis} has queued commands")
            self._check_estop()
            if rt.fault:
                raise SelfCheckError(f"axis {axis} has an uncleared fault: {rt.fault}")

            # 1. limit switches sane
            snap = self._snapshot(max_age=0.0)
            rep.limit_negative_before = snap.limit_negative[axis]
            rep.limit_positive_before = snap.limit_positive[axis]
            if snap.limit_negative[axis] and snap.limit_positive[axis]:
                raise SelfCheckError(
                    f"axis {axis}: both limit switches active (wiring fault?)"
                )

            # 2. home to the primary limit (machine zero)
            self.home(axis, wait=True)

            # 3. creep toward the far switch until it trips
            home_negative = rt.cfg.homing.direction == HOMING_NEGATIVE
            direction = 1 if home_negative else -1
            creep = _Cmd(
                kind=_KIND_CREEP,
                handle=MoveHandle(axis, _KIND_CREEP,
                                  f"range search dir={direction:+d}"),
                target=sc.max_search_mm,
                speed=sc.creep_speed,
                accel=rt.cfg.defaults.accel,
                decel=rt.cfg.defaults.decel,
                direction=direction,
            )
            self._enqueue(rt, creep, validate_target=False)
            creep.handle.wait()
            far_pos = float(creep.handle.result)

            # 4. back off the safety margin
            if sc.margin_mm > 0:
                self._blocking_move(rt, delta=-direction * sc.margin_mm,
                                    speed=rt.cfg.defaults.speed)

            # 5. derive soft limits in the homed frame
            span = abs(far_pos)
            soft_lo, soft_hi = sc.margin_mm, span - sc.margin_mm
            if soft_lo >= soft_hi:
                raise SelfCheckError(
                    f"axis {axis}: discovered span {span:.3f} mm too small for "
                    f"margin {sc.margin_mm} mm"
                )
            with self._state_lock:
                if home_negative:
                    rt.soft_min, rt.soft_max = soft_lo, soft_hi
                else:
                    rt.soft_min, rt.soft_max = -soft_hi, -soft_lo
                rt.cfg.soft_limits.min = rt.soft_min
                rt.cfg.soft_limits.max = rt.soft_max
                rt.cfg.soft_limits.span_mm = span
                rt.cfg.soft_limits.discovered_at = _now_iso()

            if sc.return_to_min:
                park = rt.soft_min if home_negative else rt.soft_max
                self.move_absolute(axis, park, wait=True)

            rep.span_mm = span
            rep.soft_limit_min = rt.soft_min
            rep.soft_limit_max = rt.soft_max
            rep.passed = True
        except MotionError as exc:
            rep.error = f"{type(exc).__name__}: {exc}"
        rep.duration_s = round(time.monotonic() - t0, 3)
        return rep

    # ------------------------------------------------------------------ motion
    def move_absolute(self, axis: int, position: float, *,
                      speed: Optional[float] = None, accel: Optional[float] = None,
                      decel: Optional[float] = None, wait: bool = True,
                      timeout: Optional[float] = None) -> MoveHandle:
        return self._submit_move(axis, position, relative=False, speed=speed,
                                 accel=accel, decel=decel, wait=wait, timeout=timeout)

    def move_relative(self, axis: int, delta: float, *,
                      speed: Optional[float] = None, accel: Optional[float] = None,
                      decel: Optional[float] = None, wait: bool = True,
                      timeout: Optional[float] = None) -> MoveHandle:
        return self._submit_move(axis, delta, relative=True, speed=speed,
                                 accel=accel, decel=decel, wait=wait, timeout=timeout)

    def _submit_move(self, axis: int, value: float, *, relative: bool,
                     speed: Optional[float], accel: Optional[float],
                     decel: Optional[float], wait: bool,
                     timeout: Optional[float]) -> MoveHandle:
        rt = self._axis(axis)
        d = rt.cfg.defaults
        cmd = _Cmd(
            kind=_KIND_MOVE,
            handle=MoveHandle(axis, _KIND_MOVE,
                              f"{'rel' if relative else 'abs'} {value:+.3f}"),
            target=value,
            relative=relative,
            speed=speed if speed is not None else d.speed,
            accel=accel if accel is not None else d.accel,
            decel=decel if decel is not None else d.decel,
        )
        self._validate_params(rt, cmd)
        self._enqueue(rt, cmd, validate_target=True)
        if wait:
            cmd.handle.wait(timeout)
        return cmd.handle

    def jog(self, axis: int, direction: int, speed: Optional[float] = None) -> MoveHandle:
        """Emulated continuous jog (FR-MOVE-05): bounded move toward the
        soft-limit boundary; end it with ``jog_stop``."""
        if direction not in (1, -1):
            raise MotionError("jog direction must be +1 or -1")
        rt = self._axis(axis)
        jog_speed = speed if speed is not None else rt.cfg.jog.speed
        if rt.homed and self._limits_known(rt):
            boundary = rt.soft_max if direction > 0 else rt.soft_min
            return self.move_absolute(axis, float(boundary), speed=jog_speed, wait=False)
        return self.move_relative(axis, direction * rt.cfg.jog.max_step_unhomed,
                                  speed=jog_speed, wait=False)

    def jog_stop(self, axis: int) -> None:
        self.stop(axis, mode="controlled")

    def wait_for(self, axis: int, timeout: Optional[float] = None) -> bool:
        """Block until the axis queue is empty and the axis is stopped."""
        rt = self._axis(axis)
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            idle = rt.queue.empty() and self._is_stopped(axis)
            if idle:
                return True
            if deadline is not None and time.monotonic() > deadline:
                return False
            time.sleep(self._cfg.poll_interval_s)

    # ------------------------------------------------------------------- queue
    def get_queue(self, axis: int) -> list[str]:
        rt = self._axis(axis)
        with rt.queue.mutex:
            return [cmd.handle.description for cmd in list(rt.queue.queue)]

    def clear_queue(self, axis: Optional[int] = None) -> None:
        for rt in self._select_axes(axis):
            self._flush_axis(rt, MoveCancelledError("queue cleared"), abort_running=False)

    # ------------------------------------------------------------------ safety
    def stop(self, axis: Optional[int] = None, mode: str = "controlled") -> None:
        immediate = mode == "immediate"
        logger.info("stop axis=%s mode=%s", axis if axis is not None else "all", mode)
        targets = self._select_axes(axis)
        # hardware stop first (NFR-02), then queue flush
        with self._ll_lock:
            for rt in targets:
                self._ll_stop(rt.cfg.index, immediate=immediate)
        for rt in targets:
            self._flush_axis(rt, MoveCancelledError(f"stop({mode})"))

    def estop(self) -> None:
        """Software e-stop: the vendor global Stop_Run is unused, so this is an
        immediate per-axis stop across all axes plus a latched lockout
        (FR-STOP-02)."""
        logger.warning("ESTOP")
        with self._state_lock:
            self._estop_latched = True
        with self._ll_lock:
            for rt in self._axes.values():
                self._ll_stop(rt.cfg.index, immediate=True)
        for rt in self._axes.values():
            self._flush_axis(rt, EStopActiveError("emergency stop"))

    def clear_estop(self) -> None:
        snap = self._snapshot(max_age=0.0)
        if snap.hardware_estop:
            raise EStopActiveError("hardware e-stop input still active")
        with self._state_lock:
            self._estop_latched = False
        logger.info("estop cleared")

    def clear_fault(self, axis: int) -> None:
        rt = self._axis(axis)
        with self._state_lock:
            logger.info("clear_fault axis=%s (was: %s)", axis, rt.fault)
            rt.fault = None

    def set_soft_limits_enabled(self, enabled: bool) -> None:
        with self._state_lock:
            self._soft_limits_enabled = enabled
        logger.warning("soft limits %s", "enabled" if enabled else "DISABLED")

    # ------------------------------------------------------------------ status
    def get_status(self) -> ControllerStatus:
        snap = self._snapshot(max_age=0.0)
        axes: dict[int, AxisStatus] = {}
        with self._state_lock:
            for idx, rt in self._axes.items():
                axes[idx] = AxisStatus(
                    index=idx,
                    name=rt.cfg.name,
                    position=snap.positions[idx],
                    encoder_position=snap.encoder_positions.get(idx),
                    speed=snap.speeds[idx],
                    homed=rt.homed,
                    busy=not (rt.queue.empty() and self._is_stopped(idx)),
                    queue_depth=rt.queue.qsize(),
                    limit_negative=snap.limit_negative[idx],
                    limit_positive=snap.limit_positive[idx],
                    soft_limit_min=rt.soft_min,
                    soft_limit_max=rt.soft_max,
                    fault=rt.fault,
                )
            return ControllerStatus(
                connected=self._connected,
                estop_active=self._estop_latched or snap.hardware_estop,
                hardware_estop=snap.hardware_estop,
                run_state=snap.run_state,
                axes=axes,
            )

    def get_position(self, axis: int) -> float:
        self._axis(axis)
        return self._snapshot().positions[axis]

    def start_polling(self, rate_hz: float,
                      callback: Callable[[ControllerStatus], None]) -> None:
        if rate_hz <= 0:
            raise MotionError("rate_hz must be > 0")
        self.stop_polling()
        self._poll_stop.clear()

        def _loop() -> None:
            period = 1.0 / rate_hz
            while not self._poll_stop.wait(period):
                try:
                    callback(self.get_status())
                except Exception:  # callback bugs must not kill polling
                    logger.exception("status polling callback raised")

        self._poll_thread = threading.Thread(target=_loop, name="fmc-status-poll",
                                             daemon=True)
        self._poll_thread.start()

    def stop_polling(self) -> None:
        self._poll_stop.set()
        if self._poll_thread is not None:
            self._poll_thread.join(timeout=2.0)
            self._poll_thread = None

    def capabilities(self) -> dict[str, Any]:
        return {
            "axes": sorted(self._axes),
            "max_axes": 6,
            "interpolation": False,       # deferred (PRD Q4)
            "continuous_jog": False,      # emulated (PRD Q6)
            "encoder_feedback": True,
            "global_stop": False,         # vendor Stop_Run unused; per-axis loop
            "sim": self.is_sim,
        }

    @property
    def is_sim(self) -> bool:
        return False

    # ============================================================= internals
    def _axis(self, axis: int) -> _AxisRuntime:
        try:
            return self._axes[axis]
        except KeyError:
            raise MotionError(f"axis {axis} is not configured/enabled") from None

    def _select_axes(self, axis: Optional[int]) -> list[_AxisRuntime]:
        return [self._axis(axis)] if axis is not None else list(self._axes.values())

    def _limits_known(self, rt: _AxisRuntime) -> bool:
        return rt.soft_min is not None and rt.soft_max is not None

    def _check_estop(self) -> None:
        with self._state_lock:
            if self._estop_latched:
                raise EStopActiveError("software e-stop latched; call clear_estop()")

    def _validate_params(self, rt: _AxisRuntime, cmd: _Cmd) -> None:
        m = rt.cfg.max
        if not 0 < cmd.speed <= m.speed:
            raise SoftLimitError(f"axis {rt.cfg.index}: speed {cmd.speed} outside (0, {m.speed}]")
        if not 0 < cmd.accel <= m.accel:
            raise SoftLimitError(f"axis {rt.cfg.index}: accel {cmd.accel} outside (0, {m.accel}]")
        if not 0 < cmd.decel <= m.decel:
            raise SoftLimitError(f"axis {rt.cfg.index}: decel {cmd.decel} outside (0, {m.decel}]")

    def _enqueue(self, rt: _AxisRuntime, cmd: _Cmd, *, validate_target: bool) -> None:
        if not self._connected:
            raise ControllerConnectionError("not connected")
        self._check_estop()
        with self._state_lock:
            if rt.fault:
                raise LimitHitError(
                    f"axis {rt.cfg.index} is faulted ({rt.fault}); call clear_fault()"
                )
            if validate_target:
                if not rt.homed and not rt.cfg.allow_unhomed:
                    raise NotHomedError(
                        f"axis {rt.cfg.index} is not homed (set allow_unhomed to override)"
                    )
                end = self._predict_end(rt, cmd)
                if self._soft_limits_enabled and self._limits_known(rt):
                    lo, hi = rt.soft_min, rt.soft_max
                    if not (lo - 1e-9 <= end <= hi + 1e-9):  # type: ignore[operator]
                        raise SoftLimitError(
                            f"axis {rt.cfg.index}: target {end:.3f} outside "
                            f"[{lo:.3f}, {hi:.3f}]"
                        )
                rt.predicted_end = end
            rt.queue.put(cmd)
        logger.debug("enqueue axis=%s %s", rt.cfg.index, cmd.handle.description)

    def _predict_end(self, rt: _AxisRuntime, cmd: _Cmd) -> float:
        base = rt.predicted_end
        if base is None or (rt.queue.empty() and self._is_stopped(rt.cfg.index)):
            base = self._snapshot().positions[rt.cfg.index]
        return base + cmd.target if cmd.relative else cmd.target

    def _flush_axis(self, rt: _AxisRuntime, error: MotionError,
                    abort_running: bool = True) -> None:
        cancelled: list[MoveHandle] = []
        with rt.queue.mutex:
            while rt.queue.queue:
                cancelled.append(rt.queue.queue.popleft().handle)
        for handle in cancelled:
            handle._finish_error(error, cancelled=True)
        with self._state_lock:
            rt.predicted_end = None
        if abort_running:
            rt.abort.set()

    # ----------------------------------------------------------- worker thread
    def _worker_loop(self, rt: _AxisRuntime) -> None:
        while not self._shutdown.is_set():
            try:
                cmd = rt.queue.get(timeout=0.1)
            except queue_mod.Empty:
                continue
            if cmd.handle.finished:  # cancelled while queued
                continue
            rt.abort.clear()
            cmd.handle._start()
            try:
                self._dispatch(rt, cmd)
            except MoveCancelledError as exc:
                cmd.handle._finish_error(exc, cancelled=True)
            except MotionError as exc:
                logger.error("axis %s %s failed: %s", rt.cfg.index,
                             cmd.handle.description, exc)
                cmd.handle._finish_error(exc)
                with self._state_lock:
                    if isinstance(exc, (LimitHitError, MoveTimeoutError)):
                        rt.fault = str(exc)
                self._flush_axis(rt, MoveCancelledError(f"previous command failed: {exc}"),
                                 abort_running=False)
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception("axis %s worker crashed on %s", rt.cfg.index,
                                 cmd.handle.description)
                cmd.handle._finish_error(MotionError(str(exc)))

    def _dispatch(self, rt: _AxisRuntime, cmd: _Cmd) -> None:
        axis = rt.cfg.index
        self._check_estop()
        with self._state_lock:
            if rt.fault:
                raise MoveCancelledError(f"axis {axis} faulted: {rt.fault}")

        if cmd.kind == _KIND_HOME:
            self._exec_home(rt, cmd)
        elif cmd.kind == _KIND_MOVE:
            self._exec_move(rt, cmd)
        elif cmd.kind == _KIND_CREEP:
            self._exec_creep(rt, cmd)
        else:  # pragma: no cover
            raise MotionError(f"unknown command kind {cmd.kind}")

    def _exec_home(self, rt: _AxisRuntime, cmd: _Cmd) -> None:
        axis = rt.cfg.index
        h = rt.cfg.homing
        logger.info("home axis=%s dir=%s speed=%s", axis, h.direction, h.speed)
        with self._ll_lock:
            self._ll_home(axis, h.speed, h.accdec, h.fall_step,
                          direction_positive=h.direction != HOMING_NEGATIVE)
        self._poll_until_stopped(rt, cmd, check_limits=False)
        with self._state_lock:
            rt.homed = True
            rt.predicted_end = None
        cmd.handle._finish_ok()

    def _exec_move(self, rt: _AxisRuntime, cmd: _Cmd) -> None:
        axis = rt.cfg.index
        snap = self._snapshot(max_age=0.0)
        start = snap.positions[axis]
        target = start + cmd.target if cmd.relative else cmd.target
        direction = 1 if target >= start else -1
        # dispatch-time re-validation (FR-MOVE-03)
        with self._state_lock:
            if self._soft_limits_enabled and self._limits_known(rt):
                lo, hi = rt.soft_min, rt.soft_max
                if not (lo - 1e-9 <= target <= hi + 1e-9):  # type: ignore[operator]
                    raise SoftLimitError(
                        f"axis {axis}: dispatch target {target:.3f} outside "
                        f"[{lo:.3f}, {hi:.3f}]"
                    )
        # refuse to start a move INTO an already-active limit switch
        if direction > 0 and snap.limit_positive[axis]:
            raise LimitHitError(f"axis {axis}: positive limit active, cannot move further positive")
        if direction < 0 and snap.limit_negative[axis]:
            raise LimitHitError(f"axis {axis}: negative limit active, cannot move further negative")

        logger.debug("move axis=%s %s -> %.3f speed=%s", axis,
                     cmd.handle.description, target, cmd.speed)
        with self._ll_lock:
            self._ll_move(axis, cmd.target, cmd.speed, cmd.accel, cmd.decel,
                          relative=cmd.relative)
        self._poll_until_stopped(rt, cmd, check_limits=True, direction=direction)
        cmd.handle._finish_ok(self._snapshot(max_age=0.0).positions[axis])

    def _exec_creep(self, rt: _AxisRuntime, cmd: _Cmd) -> None:
        """Self-check range search: relative move toward the far switch; success
        when that switch trips (the library stops the axis itself — FR-HOME-07)."""
        axis = rt.cfg.index
        with self._ll_lock:
            self._ll_move(axis, cmd.direction * cmd.target, cmd.speed, cmd.accel,
                          cmd.decel, relative=True)
        deadline = self._move_deadline(rt)
        limit_key = "limit_positive" if cmd.direction > 0 else "limit_negative"
        while True:
            self._raise_if_aborted(rt)
            snap = self._snapshot()
            if getattr(snap, limit_key)[axis]:
                with self._ll_lock:
                    self._ll_stop(axis, immediate=True)
                self._wait_ll_stopped(rt)
                pos = self._snapshot(max_age=0.0).positions[axis]
                with self._state_lock:
                    rt.predicted_end = None
                cmd.handle._finish_ok(pos)
                return
            if self._is_stopped(axis):
                raise SelfCheckError(
                    f"axis {axis}: far limit switch did not trigger within "
                    f"{cmd.target} mm search distance"
                )
            if deadline is not None and time.monotonic() > deadline:
                with self._ll_lock:
                    self._ll_stop(axis, immediate=True)
                raise MoveTimeoutError(f"axis {axis}: range search watchdog expired")
            time.sleep(self._cfg.poll_interval_s)

    def _poll_until_stopped(self, rt: _AxisRuntime, cmd: _Cmd, *,
                            check_limits: bool, direction: int = 0) -> None:
        axis = rt.cfg.index
        deadline = self._move_deadline(rt)
        while True:
            self._raise_if_aborted(rt)
            if check_limits:
                snap = self._snapshot()
                tripped = (snap.limit_positive[axis] if direction > 0
                           else snap.limit_negative[axis] if direction < 0 else False)
                if tripped:
                    with self._ll_lock:
                        self._ll_stop(axis, immediate=True)
                    side = "positive" if direction > 0 else "negative"
                    raise LimitHitError(
                        f"axis {axis}: {side} limit switch tripped during move"
                    )
            if self._is_stopped(axis):
                return
            if deadline is not None and time.monotonic() > deadline:
                with self._ll_lock:
                    self._ll_stop(axis, immediate=True)
                raise MoveTimeoutError(
                    f"axis {axis}: move watchdog ({rt.cfg.move_timeout_s}s) expired"
                )
            time.sleep(self._cfg.poll_interval_s)

    def _move_deadline(self, rt: _AxisRuntime) -> Optional[float]:
        if rt.cfg.move_timeout_s is None:
            return None
        return time.monotonic() + rt.cfg.move_timeout_s

    def _raise_if_aborted(self, rt: _AxisRuntime) -> None:
        if rt.abort.is_set() or self._shutdown.is_set():
            raise MoveCancelledError("command aborted by stop/estop/disconnect")
        with self._state_lock:
            if self._estop_latched:
                raise MoveCancelledError("command aborted by e-stop")

    def _wait_ll_stopped(self, rt: _AxisRuntime, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        while not self._is_stopped(rt.cfg.index):
            if time.monotonic() > deadline:
                return
            time.sleep(self._cfg.poll_interval_s / 2)

    def _is_stopped(self, axis: int) -> bool:
        with self._ll_lock:
            return self._ll_is_stopped(axis)

    def _blocking_move(self, rt: _AxisRuntime, *, delta: float, speed: float) -> None:
        cmd = _Cmd(
            kind=_KIND_MOVE,
            handle=MoveHandle(rt.cfg.index, _KIND_MOVE, f"rel {delta:+.3f} (internal)"),
            target=delta,
            relative=True,
            speed=speed,
            accel=rt.cfg.defaults.accel,
            decel=rt.cfg.defaults.decel,
        )
        self._enqueue(rt, cmd, validate_target=False)
        cmd.handle.wait()

    def _snapshot(self, max_age: Optional[float] = None) -> LLSnapshot:
        if max_age is None:
            max_age = self._cfg.poll_interval_s / 2
        now = time.monotonic()
        cached = self._snap_cache
        if cached is not None and (now - self._snap_time) <= max_age:
            return cached
        with self._ll_lock:
            snap = self._ll_snapshot()
        self._snap_cache = snap
        self._snap_time = time.monotonic()
        return snap


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")

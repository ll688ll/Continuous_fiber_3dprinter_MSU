"""Duet 3 / RepRapFirmware motion adapter (printer HAL wide contract).

Semantics that differ from the FMC path, by design:

* ``move_async`` raises — RRF executes whole programs from its own SD card
  (``capabilities()["execution"] == "gcode_file"``); the print runner routes
  Duet jobs through the file/stream execution strategies instead of per-move
  dispatch. Jog/manual moves here are synchronous G1s.
* ``stop(CONTROLLED)`` waits the queued interactive move out: RRF has no
  flush-the-queue G-code for HTTP-issued moves (M25 pauses *file* prints
  only). Jogs are short bounded moves; anything urgent is ``estop()``.
* ``estop`` = M112 (board halts); ``clear_estop`` = M999, which REBOOTS the
  board — the adapter reconnects automatically but the machine loses its
  homed state (RRF datums do not survive reset).
* On connect the adapter reads ``move.axes`` and cross-checks the board's
  live config against the profile (steps/mm, limits, letters); findings land
  in ``drift_findings`` and preflight surfaces them per
  ``policies.config_drift`` — hand-edited SD cards get caught before motion.
"""

from __future__ import annotations

import logging
import time
from typing import Mapping, Optional, Sequence

from ....config.profile import AxisDef
from ..base_motion_controller import (
    BaseMotionController,
    MotionStatus,
    NormalizedError,
    StopMode,
)
from .client import DuetClient, DuetError

logger = logging.getLogger("printer_msu.duet")

#: state.status values that mean "motion system is executing something"
_BUSY_STATES = ("processing", "busy", "paused", "pausing", "resuming",
                "simulating")
_HALTED = "halted"


def _fmt(value: float) -> str:
    """Compact G-code number: no exponent, no trailing zeros."""
    return f"{value:.4f}".rstrip("0").rstrip(".")


class DuetMotionAdapter(BaseMotionController):
    """Drives one Duet board over HTTP for connect/status/home/jog/e-stop."""

    HOME_TIMEOUT_S = 180.0
    MOVE_TIMEOUT_S = 120.0
    RECONNECT_TIMEOUT_S = 30.0
    POLL_S = 0.5
    #: how long an initial 'idle' is distrusted after dispatch (the board
    #: takes a beat to start; instance-overridable for fast tests)
    IDLE_GRACE_S = 2.0

    def __init__(self, client: DuetClient, axes: Sequence[AxisDef]) -> None:
        self._client = client
        self._axes = {a.name: a for a in axes}
        self._letter_of = {a.name: a.letter for a in axes}
        self._name_of = {a.letter: a.name for a in axes}
        self._connected = False
        #: config drift found at connect; preflight applies policies.config_drift
        self.drift_findings: list[str] = []
        #: set by build_machine when the profile has duet tool hardware;
        #: full_status() then carries a "tools" block for the UI poller
        self.tool_controller = None

    @property
    def client(self) -> DuetClient:
        """The shared HTTP client (execution strategies ride the same lock)."""
        return self._client

    # ------------------------------------------------------------- lifecycle
    def connect(self) -> None:
        self._client.connect()
        self._connected = True
        self.drift_findings = self._check_drift()
        for finding in self.drift_findings:
            logger.warning("config drift: %s", finding)

    def disconnect(self) -> None:
        self._connected = False

    def _check_drift(self) -> list[str]:
        """Profile vs live board config (M92/M208 essentials)."""
        findings: list[str] = []
        model_axes = {ax.get("letter"): ax
                      for ax in (self._client.model("move.axes") or [])}
        for name, axis in self._axes.items():
            live = model_axes.get(axis.letter)
            if live is None:
                findings.append(
                    f"axis {name!r}: letter {axis.letter!r} is not configured "
                    f"on the board (run apply-config)")
                continue
            steps = live.get("stepsPerMm")
            if steps is not None and axis.steps_per_mm is not None and \
                    abs(steps - axis.steps_per_mm) > axis.steps_per_mm * 1e-3:
                findings.append(
                    f"axis {name!r}: board stepsPerMm={steps} vs profile "
                    f"{axis.steps_per_mm} (scale mismatch!)")
            if axis.limits_mm is not None:
                lo, hi = axis.limits_mm
                b_lo, b_hi = live.get("min"), live.get("max")
                if b_lo is not None and abs(b_lo - lo) > 0.01:
                    findings.append(f"axis {name!r}: board min={b_lo} vs "
                                    f"profile {lo}")
                if b_hi is not None and abs(b_hi - hi) > 0.01:
                    findings.append(f"axis {name!r}: board max={b_hi} vs "
                                    f"profile {hi}")
        return findings

    # ---------------------------------------------------------------- status
    def _state_status(self) -> str:
        return str(self._client.model("state.status") or "unknown")

    def _model_axes(self) -> list[dict]:
        return list(self._client.model("move.axes") or [])

    def get_status(self) -> MotionStatus:
        status = self._state_status()
        positions: dict[str, float] = {}
        speeds: dict[str, float] = {}
        for ax in self._model_axes():
            name = self._name_of.get(ax.get("letter"))
            if name is None:
                continue
            positions[name] = float(ax.get("machinePosition", 0.0))
            speeds[name] = 0.0  # RRF reports whole-move speed, not per-axis
        last_error = None
        if status == _HALTED:
            last_error = NormalizedError(
                code="HALTED", message="board is halted (M112); clear_estop "
                                       "sends M999 and reboots it")
        return MotionStatus(
            positions=positions,
            speeds=speeds,
            emergency_active=status == _HALTED,
            run_state="running" if status in _BUSY_STATES else status,
            last_error=last_error,
        )

    def full_status(self) -> dict:
        status = self._state_status()
        moving = status in _BUSY_STATES
        axes = {}
        for ax in self._model_axes():
            name = self._name_of.get(ax.get("letter"))
            if name is None:
                continue
            axes[name] = {
                "position": float(ax.get("machinePosition", 0.0)),
                "speed": 0.0,
                "homed": bool(ax.get("homed", False)),
                "busy": moving,
                "fault": None,
            }
        out = {"axes": axes, "estop": status == _HALTED,
               "connected": self._connected}
        if self.tool_controller is not None:
            try:
                out["tools"] = dict(self.tool_controller.temperatures_dict())
            except Exception:  # a failed temp read must not kill the poller
                logger.exception("tool status read failed")
        return out

    def homed_axes(self) -> set[str]:
        return {self._name_of[ax.get("letter")]
                for ax in self._model_axes()
                if ax.get("letter") in self._name_of and ax.get("homed")}

    def max_speeds(self) -> dict[str, float]:
        return {name: a.max_speed_mm_s for name, a in self._axes.items()
                if a.max_speed_mm_s is not None}

    def soft_limits(self) -> dict[str, tuple[float, float] | None]:
        return {name: a.limits_mm for name, a in self._axes.items()}

    def capabilities(self) -> dict:
        return {
            "axis_names": sorted(self._axes),
            "execution": "gcode_file",   # prints run via the Duet strategies
            "interpolation": True,
            "arcs": True,
            "lookahead_moves": 60,       # host sync is impossible finer than this
        }

    # ---------------------------------------------------------------- motion
    def home(self, axes: Optional[list[str]] = None) -> None:
        """G28. A full home uses bare G28 (the board's homeall.g runs tool-Z
        FIRST, clearing the print plane — SRS D11); partial homes issue one
        G28 per axis in the caller's order."""
        if axes is None or set(axes) == set(self._axes):
            lines = ["G28"]
            wanted = list(self._axes)
        else:
            for a in axes:
                self._require_axis(a)
            lines = [f"G28 {self._letter_of[a]}" for a in axes]
            wanted = list(axes)
        self._send_lines(lines)
        self._wait_idle(self.HOME_TIMEOUT_S, context="homing")
        self._raise_on_error_reply("homing")
        missing = [a for a in wanted if a not in self.homed_axes()]
        if missing:
            raise DuetError(f"homing finished but axes not homed: {missing}")

    def move_absolute(self, axes_positions: Mapping[str, float], feed: float,
                      accel: float | None = None,
                      decel: float | None = None) -> None:
        self._move(axes_positions, feed, relative=False)

    def move_relative(self, axes_deltas: Mapping[str, float], feed: float,
                      accel: float | None = None,
                      decel: float | None = None) -> None:
        self._move(axes_deltas, feed, relative=True)

    def _move(self, values: Mapping[str, float], feed_mm_s: float,
              *, relative: bool) -> None:
        if not values:
            return
        self._check_soft_limits(values, relative=relative)
        words = " ".join(f"{self._require_axis(n)}{_fmt(v)}"
                         for n, v in values.items())
        gline = f"G1 {words} F{_fmt(feed_mm_s * 60.0)}"  # RRF F is mm/min
        lines = ["G91", gline, "G90"] if relative else ["G90", gline]
        self._send_lines(lines)
        self._wait_idle(self.MOVE_TIMEOUT_S, context=f"move {words}")
        # RRF reports refusals (e.g. "insufficient axes homed") only via
        # rr_reply — without this, a refused move would look like success
        self._raise_on_error_reply(f"move {words}")

    def move_async(self, axes_positions: Mapping[str, float], feed: float,
                   accel: float | None = None, decel: float | None = None):
        raise NotImplementedError(
            "the Duet backend executes whole programs (capabilities()"
            "['execution'] == 'gcode_file'); prints go through the Duet "
            "execution strategies, not per-move dispatch")

    # ---------------------------------------------------------------- safety
    def stop(self, mode: StopMode = StopMode.CONTROLLED) -> None:
        if mode is StopMode.IMMEDIATE:
            self.estop()
            return
        # RRF has no controlled queue-flush for interactive moves; jogs are
        # short bounded moves, so waiting them out IS the controlled stop.
        logger.info("controlled stop: waiting for queued interactive moves "
                    "to finish (RRF cannot flush them; e-stop for immediate)")
        self._wait_idle(self.MOVE_TIMEOUT_S, context="controlled stop")

    def estop(self) -> None:
        logger.warning("ESTOP -> M112")
        self._client.send_emergency("M112")

    def clear_estop(self) -> None:
        """M999: the board REBOOTS. Reconnects when it comes back; homed
        state is lost (re-home before the next move)."""
        logger.warning("clear_estop -> M999 (board reboots; re-home after)")
        try:
            self._client.send_emergency("M999")
        except DuetError:
            pass  # the reset usually kills the HTTP reply mid-flight
        deadline = time.monotonic() + self.RECONNECT_TIMEOUT_S
        while True:
            time.sleep(1.0)
            try:
                self._client.connect()
                self._connected = True
                return
            except DuetError:
                if time.monotonic() > deadline:
                    raise DuetError(
                        f"board did not come back within "
                        f"{self.RECONNECT_TIMEOUT_S:.0f} s of M999")

    # --------------------------------------------------------------- helpers
    def _check_soft_limits(self, values: Mapping[str, float],
                           *, relative: bool) -> None:
        """Refuse out-of-envelope interactive moves HOST-SIDE.

        Lab shakeout 2026-08-30: RRF's default ``M564 S1`` does not reject a
        target beyond M208 — it SILENTLY CLAMPS the move to the limit (same
        failure family as the FMC's silent-ignore: safe machine, lying
        software). The board's clamp stays as the physical backstop; this
        check makes the refusal loud. Prints are covered separately by the
        preflight envelope walk."""
        current: dict[str, float] = {}
        if relative:
            for ax in self._model_axes():
                name = self._name_of.get(ax.get("letter"))
                if name is not None:
                    current[name] = float(ax.get("machinePosition", 0.0))
        for name, value in values.items():
            axis = self._axes.get(name)
            if axis is None or axis.limits_mm is None:
                continue
            lo, hi = axis.limits_mm
            target = current.get(name, 0.0) + value if relative else value
            if not (lo - 1e-6 <= target <= hi + 1e-6):
                raise DuetError(
                    f"{name}={target:.3f} is outside the soft limits "
                    f"[{lo:.3f}, {hi:.3f}] mm - refused host-side (RRF "
                    f"M564 S1 would silently clamp it, not reject it)")

    def _raise_on_error_reply(self, context: str) -> None:
        try:
            reply = self._client.reply()
        except DuetError:
            return  # a lost reply is not proof of failure
        for line in reply.splitlines():
            if line.strip().lower().startswith("error"):
                raise DuetError(f"{context}: board replied {line.strip()!r}")

    def _require_axis(self, name: str) -> str:
        try:
            return self._letter_of[name]  # type: ignore[return-value]
        except KeyError:
            raise KeyError(f"unknown axis {name!r}; configured: "
                           f"{sorted(self._axes)}") from None

    def _send_lines(self, lines: Sequence[str]) -> None:
        payload = "\n".join(lines)
        need = len(payload.encode("ascii", "replace")) + 1
        if not self._client.wait_for_space(need):
            raise DuetError("board input buffer never freed up "
                            "(is a job consuming it?)")
        self._client.send(payload)

    def _wait_idle(self, timeout_s: float, *, context: str) -> None:
        """Wait for the motion system to become idle again.

        The board takes a moment to leave 'idle' after a command is queued,
        so a grace window tolerates idle reads at the start (the FMC taught
        this lesson the hard way; RRF is fast but the pattern stands)."""
        deadline = time.monotonic() + timeout_s
        started = time.monotonic()
        seen_busy = False
        while True:
            status = self._state_status()
            if status == _HALTED:
                raise DuetError(f"{context}: board halted (M112/fault)")
            if status in _BUSY_STATES:
                seen_busy = True
            elif status == "idle":
                if seen_busy or time.monotonic() - started > self.IDLE_GRACE_S:
                    return
            if time.monotonic() > deadline:
                raise DuetError(f"{context}: not idle after {timeout_s:.0f} s")
            time.sleep(self.POLL_S)

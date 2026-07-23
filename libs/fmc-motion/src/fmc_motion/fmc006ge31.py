"""FMC006GE31 hardware backend (real mode).

Status: M2 groundwork — the ctypes binding is complete per the vendor SDK
manual V1.2, but it is UNTESTED against real hardware until milestone M3
bench-up. The sim backend (`mode="sim"`) is the supported path for development.

The vendor DLL is Windows-only; this module imports everywhere but
``connect()`` raises on non-Windows platforms. The 64-/32-bit DLL is chosen to
match the Python interpreter.
"""

from __future__ import annotations

import ctypes
import sys
import threading
from ctypes import Structure, byref, c_char_p, c_float, c_int32, c_uint32
from pathlib import Path
from typing import Optional

from .config import ControllerConfig
from .errors import (
    ControllerConnectionError,
    DeviceBusyError,
    HardwareFaultError,
    describe_vendor_code,
)
from ._engine import EngineController, LLSnapshot

_MAX_AXIS = 6

# vendor Jog_Single_Axis mode values (SDK manual V1.2)
_MODE_RELATIVE = 1
_MODE_ABSOLUTE = 2
# vendor Stop_Single_Axis mode values
_STOP_DECEL = 1
_STOP_IMMEDIATE = 2
# vendor Home_Single_Axis direction values
_HOME_POSITIVE_LIMIT = 1
_HOME_NEGATIVE_LIMIT = 2
# vendor emergencyStatus: 1 = not triggered, 2 = triggered
_ESTOP_TRIGGERED = 2


class _MachineStatus(Structure):
    _fields_ = [
        ("encoderRealPos", c_float * _MAX_AXIS),
        ("realPos", c_float * _MAX_AXIS),
        ("realSpeed", c_float * _MAX_AXIS),
        ("inputStatus", c_uint32),
        ("outputStatus", c_uint32),
        ("limitNStatus", c_uint32),
        ("limitPStatus", c_uint32),
        ("machineRunStatus", c_uint32),
        ("axisStatus", c_uint32 * _MAX_AXIS),
        ("homeStatus", c_uint32),
        ("batStatus", c_uint32),
        ("emergencyStatus", c_uint32),
    ]


def vendor_dll_path() -> Path:
    arch = "win64" if sys.maxsize > 2**32 else "win32"
    return Path(__file__).resolve().parent / "vendor" / arch / "FMC006GE31.dll"


class FMC006GE31Controller(EngineController):
    """Real-hardware backend for FMC006GE31 controllers (Windows only)."""

    def __init__(self, config: ControllerConfig) -> None:
        super().__init__(config)
        self._dll: Optional[ctypes.WinDLL] = None
        self._id = config.connection.device_id
        self._status_buf = _MachineStatus()
        self._dll_lock = threading.Lock()

    # ------------------------------------------------------------------ helpers
    def _check(self, rc: int, call: str) -> None:
        if rc == 0:
            return
        message = f"{call} failed: rc={rc} ({describe_vendor_code(rc)})"
        if rc == -10:
            raise DeviceBusyError(message)
        if rc in (-1, -5, -6, -7):
            raise ControllerConnectionError(message, vendor_code=rc)
        raise HardwareFaultError(message, vendor_code=rc)

    def _lib(self) -> ctypes.WinDLL:
        if self._dll is None:
            raise ControllerConnectionError("not connected")
        return self._dll

    # ------------------------------------------------------------ ll interface
    def _ll_connect(self) -> None:
        if sys.platform != "win32":
            raise ControllerConnectionError(
                "FMC006GE31 real mode requires Windows (vendor DLL); use mode='sim'"
            )
        dll_path = vendor_dll_path()
        if not dll_path.exists():
            raise ControllerConnectionError(f"vendor DLL not found: {dll_path}")
        dll = ctypes.WinDLL(str(dll_path))
        c = self._cfg.connection
        rc = dll.FMC006GE31_Open_Device(
            self._id, c_char_p(c.ip.encode("utf-8")), c.port, c.tcp_mode
        )
        if rc != 0:
            raise ControllerConnectionError(
                f"FMC006GE31_Open_Device({c.ip}:{c.port}) failed: rc={rc} "
                f"({describe_vendor_code(rc)})",
                vendor_code=rc,
            )
        self._dll = dll

    def _ll_disconnect(self) -> None:
        if self._dll is not None:
            # vendor requires Close_Device or the next Open_Device fails
            self._dll.FMC006GE31_Close_Device(self._id)
            self._dll = None

    def _ll_move(self, axis: int, value: float, speed: float, accel: float,
                 decel: float, relative: bool) -> None:
        with self._dll_lock:
            rc = self._lib().FMC006GE31_Jog_Single_Axis(
                self._id, axis, c_float(value), c_float(speed),
                c_float(accel), c_float(decel),
                _MODE_RELATIVE if relative else _MODE_ABSOLUTE,
            )
        self._check(rc, "FMC006GE31_Jog_Single_Axis")

    def _ll_home(self, axis: int, speed: float, accdec: float, fall_step: float,
                 direction_positive: bool) -> None:
        with self._dll_lock:
            rc = self._lib().FMC006GE31_Home_Single_Axis(
                self._id, axis, c_float(speed), c_float(accdec),
                c_float(fall_step),
                _HOME_POSITIVE_LIMIT if direction_positive else _HOME_NEGATIVE_LIMIT,
            )
        self._check(rc, "FMC006GE31_Home_Single_Axis")

    def _ll_is_stopped(self, axis: int) -> bool:
        with self._dll_lock:
            return self._lib().FMC006GE31_Check_Axis_Is_Stop(self._id, axis) == 1

    def _ll_stop(self, axis: int, immediate: bool) -> None:
        with self._dll_lock:
            rc = self._lib().FMC006GE31_Stop_Single_Axis(
                self._id, axis, _STOP_IMMEDIATE if immediate else _STOP_DECEL
            )
        self._check(rc, "FMC006GE31_Stop_Single_Axis")

    def _ll_snapshot(self) -> LLSnapshot:
        with self._dll_lock:
            rc = self._lib().FMC006GE31_Get_Machine_Status(
                self._id, byref(self._status_buf)
            )
            self._check(rc, "FMC006GE31_Get_Machine_Status")
            ms = self._status_buf
            positions = {i: float(ms.realPos[i]) for i in self._axes}
            encoders = {i: float(ms.encoderRealPos[i]) for i in self._axes}
            speeds = {i: abs(float(ms.realSpeed[i])) for i in self._axes}
            lim_n = {i: bool(ms.limitNStatus & (1 << i)) for i in self._axes}
            lim_p = {i: bool(ms.limitPStatus & (1 << i)) for i in self._axes}
            return LLSnapshot(
                positions=positions,
                encoder_positions=encoders,
                speeds=speeds,
                limit_negative=lim_n,
                limit_positive=lim_p,
                hardware_estop=ms.emergencyStatus == _ESTOP_TRIGGERED,
                run_state="running" if any(
                    not bool(self._lib().FMC006GE31_Check_Axis_Is_Stop(self._id, i))
                    for i in self._axes
                ) else "idle",
            )

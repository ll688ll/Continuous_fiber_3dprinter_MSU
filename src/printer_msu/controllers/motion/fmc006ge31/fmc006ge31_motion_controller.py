from __future__ import annotations

import sys
from typing import Mapping, Optional

import ctypes

from ..base_motion_controller import BaseMotionController, MotionStatus, StopMode
from . import vendor_dll_path


class FMC006GE31MotionController(BaseMotionController):
    """
    Motion controller plugin (skeleton) for FMC006GE31-type devices.

    Implementation note:
    - The vendor DLL is loaded at connect-time, not import-time.
    - This keeps the module importable on non-Windows machines for development/tests.
    """

    def __init__(self, *, device_id: int, ip: str, port: int, mode: int = 0) -> None:
        self._device_id = device_id
        self._ip = ip
        self._port = port
        self._mode = mode
        self._dll: ctypes.LibraryLoader | None = None

    def connect(self) -> None:
        if sys.platform != "win32":
            raise RuntimeError("FMC006GE31MotionController requires Windows (vendor DLL).")

        dll = vendor_dll_path()
        if not dll.exists():
            raise FileNotFoundError(f"Vendor DLL not found: {dll}")

        self._dll = ctypes.windll.LoadLibrary(str(dll))
        rc = self._dll.FMC006GE31_Open_Device(
            self._device_id,
            ctypes.c_char_p(self._ip.encode("utf-8")),
            self._port,
            self._mode,
        )
        if rc != 0:
            raise RuntimeError(f"FMC006GE31_Open_Device failed: rc={rc}")

    def disconnect(self) -> None:
        if self._dll is None:
            return
        self._dll.FMC006GE31_Close_Device(self._device_id)
        self._dll = None

    def get_status(self) -> MotionStatus:
        # Placeholder: map vendor machine_status struct to MotionStatus
        return MotionStatus(positions={}, speeds={}, emergency_active=False, run_state="unknown")

    def home(self, axes: Optional[list[str]] = None) -> None:
        raise NotImplementedError

    def move_absolute(
        self,
        axes_positions: Mapping[str, float],
        feed: float,
        accel: float | None = None,
        decel: float | None = None,
    ) -> None:
        raise NotImplementedError

    def move_relative(
        self,
        axes_deltas: Mapping[str, float],
        feed: float,
        accel: float | None = None,
        decel: float | None = None,
    ) -> None:
        raise NotImplementedError

    def stop(self, mode: StopMode = StopMode.CONTROLLED) -> None:
        raise NotImplementedError

    def estop(self) -> None:
        raise NotImplementedError



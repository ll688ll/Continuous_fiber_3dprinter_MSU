"""Exception hierarchy for fmc_motion (PRD section 5)."""

from __future__ import annotations

from typing import Optional


class MotionError(Exception):
    """Base class for all fmc_motion errors."""


class ControllerConnectionError(MotionError):
    """Connecting to / communicating with the controller failed."""

    def __init__(self, message: str, vendor_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.vendor_code = vendor_code


class NotHomedError(MotionError):
    """Motion was requested on an axis that has not been homed."""


class SoftLimitError(MotionError):
    """A commanded target lies outside the axis working range."""


class LimitHitError(MotionError):
    """A limit switch triggered unexpectedly during motion."""


class EStopActiveError(MotionError):
    """A software or hardware emergency stop is active."""


class DeviceBusyError(MotionError):
    """The controller rejected a command because the axis is busy (vendor -10)."""


class HardwareFaultError(MotionError):
    """The vendor library returned an error code."""

    def __init__(self, message: str, vendor_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.vendor_code = vendor_code


class SelfCheckError(MotionError):
    """A self-check step failed."""


class MoveTimeoutError(MotionError):
    """A move did not complete within the configured watchdog timeout."""


class MoveCancelledError(MotionError):
    """A queued or running move was cancelled by stop/estop/clear_queue."""


class ConfigError(MotionError):
    """Configuration is invalid (FR-CFG-04)."""


#: Vendor return-code table from FMC006GE31 SDK manual V1.2.
VENDOR_RETURN_CODES: dict[int, str] = {
    0: "success",
    -1: "connection failed",
    -4: "data build failed",
    -5: "data send failed",
    -6: "data receive failed",
    -7: "received data error",
    -8: "null pointer",
    -10: "device busy",
}


def describe_vendor_code(code: int) -> str:
    return VENDOR_RETURN_CODES.get(code, f"unknown vendor code {code}")

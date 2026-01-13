from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class PrintJobState(str, Enum):
    IDLE = "idle"
    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass
class PrintJob:
    job_id: str
    gcode_path: str
    state: PrintJobState = PrintJobState.IDLE
    current_line: int = 0



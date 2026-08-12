from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class PrintJobState(str, Enum):
    IDLE = "idle"
    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    STOPPED = "stopped"
    ERROR = "error"


#: states a job can never leave (printer PRD FR-JOB-01)
TERMINAL_STATES = frozenset(
    {PrintJobState.COMPLETED, PrintJobState.STOPPED, PrintJobState.ERROR}
)


@dataclass
class PrintJob:
    job_id: str
    gcode_path: str
    state: PrintJobState = PrintJobState.IDLE
    current_line: int = 0

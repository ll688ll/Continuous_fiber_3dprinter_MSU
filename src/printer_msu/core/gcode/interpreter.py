from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .parser import GcodeLine


class Units(str, Enum):
    MM = "mm"
    INCH = "inch"


class DistanceMode(str, Enum):
    ABSOLUTE = "absolute"
    RELATIVE = "relative"


class ExtruderMode(str, Enum):
    ABSOLUTE = "absolute"
    RELATIVE = "relative"


@dataclass
class InterpreterState:
    units: Units = Units.MM
    distance_mode: DistanceMode = DistanceMode.ABSOLUTE
    extruder_mode: ExtruderMode = ExtruderMode.ABSOLUTE
    active_tool: int | None = None


class GcodeInterpreter:
    """
    Stateful interpreter that converts parsed G-code into motion/tool actions.

    v1 focuses on:
    - tool selection via Tn (one tool active)
    - motion via G0/G1/G2/G3 routed to motion controllers
    - temperature/fan/extruder actions routed to Tool Controller
    """

    def __init__(self) -> None:
        self.state = InterpreterState()

    def step(self, line: GcodeLine) -> None:
        """
        Consume one parsed line and update state / emit actions.

        Placeholder: action emission will be added as PrintService/ToolService are implemented.
        """
        words = line.words
        if "T" in words:
            self.state.active_tool = int(words["T"])



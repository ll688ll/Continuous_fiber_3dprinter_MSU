"""Normalized action stream — interpreter output (printer PRD FR-INT-02).

Actions are controller-agnostic and serializable; each carries the 1-based
source line it came from so logs, progress, and errors correlate back to the
file (COMMAND_LIST §10).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional


@dataclass(frozen=True)
class Action:
    line_no: int

    def describe(self) -> str:
        return type(self).__name__


@dataclass(frozen=True)
class MoveLinear(Action):
    """One straight-line segment on logical axes.

    ``axes`` holds absolute targets (mm) in G90, deltas in G91 — the streamer
    resolves deltas against live controller positions. ``feed_mm_s`` is the
    composite path feed (already converted from G-code mm/min).

    ``e_delta`` is the extrusion riding this segment, normalized to a
    RELATIVE delta (mm of filament/fiber) by the interpreter regardless of
    the file's M82/M83/G92-E state — set only when the active tool has a
    configured extruder (V2-M4), and only on the planner-synced XY component
    of split moves (extrusion must ride the line it belongs to).
    """

    axes: Mapping[str, float] = field(default_factory=dict)
    feed_mm_s: float = 0.0
    relative: bool = False
    rapid: bool = False  # came from G0
    e_delta: Optional[float] = None

    def describe(self) -> str:
        coords = " ".join(f"{a}{'+' if self.relative else '='}{v:.3f}"
                          for a, v in sorted(self.axes.items()))
        e = f" E+{self.e_delta:.3f}" if self.e_delta is not None else ""
        return (f"{'G0' if self.rapid else 'G1'} {coords}{e} "
                f"@{self.feed_mm_s:.1f}mm/s")


@dataclass(frozen=True)
class HomeAxes(Action):
    """Home the named logical axes; None = all mapped axes (G28 / preflight)."""

    axes: Optional[tuple[str, ...]] = None

    def describe(self) -> str:
        return f"home {' '.join(self.axes) if self.axes else 'all'}"


@dataclass(frozen=True)
class SelectTool(Action):
    tool_index: int = 0

    def describe(self) -> str:
        return f"T{self.tool_index}"


@dataclass(frozen=True)
class SetToolTemp(Action):
    """M104/M109 for a tool with a configured heater (V2-M4)."""

    tool_index: int = 0
    temp_c: float = 0.0
    wait: bool = False  # True for M109

    def describe(self) -> str:
        return (f"T{self.tool_index} temp -> {self.temp_c:.0f}C"
                f"{' (wait)' if self.wait else ''}")


@dataclass(frozen=True)
class SetFanSpeed(Action):
    """M106/M107 for a tool with a configured fan; value 0..1."""

    tool_index: int = 0
    value: float = 0.0

    def describe(self) -> str:
        return f"T{self.tool_index} fan -> {self.value:.0%}"


@dataclass(frozen=True)
class PauseProgram(Action):
    reason: str = "M0"

    def describe(self) -> str:
        return f"pause ({self.reason})"


@dataclass(frozen=True)
class EndProgram(Action):
    def describe(self) -> str:
        return "end of program"


@dataclass(frozen=True)
class SkippedCommand(Action):
    """A recognized command that v1 does not execute (motion-only, D2)."""

    code: str = ""
    reason: str = ""

    def describe(self) -> str:
        return f"skip {self.code} ({self.reason})"


def serialize(action: Action) -> dict[str, Any]:
    data: dict[str, Any] = {"type": type(action).__name__,
                            "line_no": action.line_no}
    if isinstance(action, MoveLinear):
        data.update(axes=dict(action.axes), feed_mm_s=action.feed_mm_s,
                    relative=action.relative, rapid=action.rapid)
        if action.e_delta is not None:
            data.update(e_delta=action.e_delta)
    elif isinstance(action, HomeAxes):
        data.update(axes=list(action.axes) if action.axes else None)
    elif isinstance(action, SelectTool):
        data.update(tool_index=action.tool_index)
    elif isinstance(action, SetToolTemp):
        data.update(tool_index=action.tool_index, temp_c=action.temp_c,
                    wait=action.wait)
    elif isinstance(action, SetFanSpeed):
        data.update(tool_index=action.tool_index, value=action.value)
    elif isinstance(action, PauseProgram):
        data.update(reason=action.reason)
    elif isinstance(action, SkippedCommand):
        data.update(code=action.code, reason=action.reason)
    return data

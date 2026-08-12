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
    """

    axes: Mapping[str, float] = field(default_factory=dict)
    feed_mm_s: float = 0.0
    relative: bool = False
    rapid: bool = False  # came from G0

    def describe(self) -> str:
        coords = " ".join(f"{a}{'+' if self.relative else '='}{v:.3f}"
                          for a, v in sorted(self.axes.items()))
        return f"{'G0' if self.rapid else 'G1'} {coords} @{self.feed_mm_s:.1f}mm/s"


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
    elif isinstance(action, HomeAxes):
        data.update(axes=list(action.axes) if action.axes else None)
    elif isinstance(action, SelectTool):
        data.update(tool_index=action.tool_index)
    elif isinstance(action, PauseProgram):
        data.update(reason=action.reason)
    elif isinstance(action, SkippedCommand):
        data.update(code=action.code, reason=action.reason)
    return data

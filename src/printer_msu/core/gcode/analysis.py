"""File-level usage analysis (printer PRD FR-PARSE-04, SRS FR-GC-020..024)."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable

from .parser import GcodeLine


@dataclass
class UsageReport:
    line_count: int = 0
    tools_referenced: set[int] = field(default_factory=set)
    axis_words: set[str] = field(default_factory=set)
    motion_segments: int = 0            # G0/G1 lines carrying axis words
    commands: Counter = field(default_factory=Counter)  # "G1" -> count
    parse_error_lines: list[int] = field(default_factory=list)
    has_arcs: bool = False
    uses_inches: bool = False
    z_or_e_before_tool: bool = False

    def summary(self) -> str:
        tools = " ".join(f"T{t}" for t in sorted(self.tools_referenced)) or "-"
        axes = " ".join(sorted(self.axis_words)) or "-"
        return (f"{self.line_count} lines, {self.motion_segments} motion "
                f"segments; tools: {tools}; axes: {axes}")


def analyze(lines: Iterable[GcodeLine]) -> UsageReport:
    report = UsageReport()
    tool_seen = False
    for line in lines:
        report.line_count += 1
        if line.errors:
            report.parse_error_lines.append(line.line_no)
        words = line.words
        # standalone Tn only: on G/M lines T is a parameter (e.g. M204 T500)
        if "T" in words and "G" not in words and "M" not in words:
            report.tools_referenced.add(int(words["T"]))
            report.commands[f"T{int(words['T'])}"] += 1
            tool_seen = True
        g = words.get("G")
        m = words.get("M")
        if g is not None:
            report.commands[f"G{g}"] += 1
            if g in (2, 3):
                report.has_arcs = True
            if g == 20:
                report.uses_inches = True
        if m is not None:
            report.commands[f"M{m}"] += 1
        for axis in "XYZE":
            if axis in words:
                report.axis_words.add(axis)
                if axis in ("Z", "E") and not tool_seen:
                    report.z_or_e_before_tool = True
        if g in (0, 1) and any(a in words for a in "XYZ"):
            report.motion_segments += 1
    return report

"""Re-emit the interpreted action stream as machine-frame G-code (V2-M3).

The Duet executes whole programs (its look-ahead planner is the point of the
platform), so instead of dispatching action-by-action, the runner renders the
interpreter's output — already machine-frame, feeds in mm/s — into a G-code
file for the board. What the board receives is exactly what preflight
envelope-walked: same actions, same targets.

Every emitted line maps back to its source G-code line (``source_lines``) and
to its byte offset (``byte_offsets``) so progress from ``job.filePosition``
can be attributed to slicer lines. ``final_targets`` carries the end position
of every axis whose last motion was absolute — the end-of-job verification
(the Duet has no per-move verification; RRF plans 60 moves ahead).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ...config.profile import MachineProfile
from ...core.model.actions import (
    Action,
    EndProgram,
    HomeAxes,
    MoveLinear,
    PauseProgram,
    SelectTool,
    SkippedCommand,
)
from ...errors import PrinterError

#: hard cap well under the board's 249-byte line limit
_MAX_LINE_BYTES = 200


class EmitError(PrinterError):
    pass


@dataclass
class EmittedProgram:
    lines: list[str] = field(default_factory=list)
    #: source G-code line per emitted line (0 = generated header/no source)
    source_lines: list[int] = field(default_factory=list)
    #: byte offset of each emitted line's START in the rendered file
    byte_offsets: list[int] = field(default_factory=list)
    #: end position per axis whose FINAL motion was absolute (verification)
    final_targets: dict[str, float] = field(default_factory=dict)
    total_bytes: int = 0

    def text(self) -> str:
        return "\n".join(self.lines) + "\n"

    def data(self) -> bytes:
        return self.text().encode("ascii")

    def source_line_at_offset(self, byte_offset: int) -> int:
        """Map a job.filePosition byte offset to the source G-code line."""
        source = 0
        for start, src in zip(self.byte_offsets, self.source_lines):
            if start > byte_offset:
                break
            if src:
                source = src
        return source


def _fmt(value: float) -> str:
    return f"{value:.4f}".rstrip("0").rstrip(".")


def emit_machine_gcode(actions: list[Action],
                       profile: MachineProfile) -> EmittedProgram:
    """Render executable actions into a board-ready program."""

    def letter(axis_name: str) -> str:
        axis = profile.axis(axis_name)
        if axis is None or not axis.letter:
            raise EmitError(
                f"axis {axis_name!r} has no controller letter in the profile "
                f"(file execution needs letter-mapped axes)")
        return axis.letter

    z_axes = {t.z_axis for t in profile.tools if t.z_axis}

    program = EmittedProgram()
    relative = False

    def emit(line: str, source_line: int = 0) -> None:
        data = line.encode("ascii", "replace")
        if len(data) > _MAX_LINE_BYTES:
            raise EmitError(f"emitted line exceeds {_MAX_LINE_BYTES} bytes: "
                            f"{line[:60]}...")
        program.byte_offsets.append(program.total_bytes)
        program.lines.append(line)
        program.source_lines.append(source_line)
        program.total_bytes += len(data) + 1  # newline

    emit(f"; printer_msu job for profile {profile.profile_name!r}")
    emit("G21")
    emit("G90")

    for action in actions:
        if isinstance(action, SkippedCommand):
            continue
        if isinstance(action, MoveLinear):
            if action.relative != relative:
                emit("G91" if action.relative else "G90", action.line_no)
                relative = action.relative
            words = " ".join(
                f"{letter(a)}{_fmt(v)}" for a, v in action.axes.items())
            code = "G0" if action.rapid else "G1"
            emit(f"{code} {words} F{_fmt(action.feed_mm_s * 60.0)}",
                 action.line_no)
            for a, v in action.axes.items():
                if action.relative:
                    # relative on an unknown base: drop from verification
                    if a in program.final_targets:
                        program.final_targets[a] += v
                else:
                    program.final_targets[a] = v
        elif isinstance(action, HomeAxes):
            if relative:
                emit("G90", action.line_no)
                relative = False
            if action.axes is None:
                emit("G28", action.line_no)  # board homeall.g runs tool-Z first
                homed = list(profile.axis_names)
            else:
                # tool-Z first (SRS D11), then the rest, caller order preserved
                selected = list(action.axes)
                ordered = ([a for a in selected if a in z_axes]
                           + [a for a in selected if a not in z_axes])
                for a in ordered:
                    emit(f"G28 {letter(a)}", action.line_no)
                homed = ordered
            for a in homed:
                # homing invalidates the tracked absolute position
                program.final_targets.pop(a, None)
        elif isinstance(action, PauseProgram):
            emit(f"M226 ; pause ({action.reason})", action.line_no)
        elif isinstance(action, SelectTool):
            # becomes a real Tn when the board defines tools (V2-M4)
            emit(f"; T{action.tool_index} (tool controller pending)",
                 action.line_no)
        elif isinstance(action, EndProgram):
            emit("; end of program", action.line_no)
        else:  # pragma: no cover - defensive
            raise EmitError(f"unemittable action {action!r}")

    if relative:
        emit("G90")
    return program

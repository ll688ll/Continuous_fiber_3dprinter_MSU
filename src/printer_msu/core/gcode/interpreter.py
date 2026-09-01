"""Stateful G-code interpreter (printer PRD FR-INT-01..07).

Consumes parsed ``GcodeLine`` records and a ``MachineProfile``; produces the
normalized action stream plus diagnostics. Motion-only per decision D2: tool /
temperature / fan / extrusion commands are recognized and skipped (policy
``tool_commands: skip``) or turned into fatal diagnostics (``error``).

Key semantics:
- modal state per SRS FR-GC-011: G20/G21, G90/G91, M82/M83, Tn, F
- F is mm/min (RepRap dialect) and is converted to mm/s for the HAL
- ``Z`` routes to the active tool's mapped z-axis (FR-INT-03)
- a move touching Z and XY together is split Z-first with a warning (§2.2)
- G28 homes logical axes (dialect extension, FR-INT-06)
- G2/G3 and G20-at-execution are fatal in v1 (D4 / §2.2)
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Optional

from ...config.profile import MachineProfile, POLICY_ERROR, POLICY_SKIP
from ...core.model.actions import (
    Action,
    EndProgram,
    HomeAxes,
    MoveLinear,
    PauseProgram,
    SelectTool,
    SkippedCommand,
)
from .parser import GcodeLine

MM_PER_MIN_TO_MM_PER_S = 1.0 / 60.0

#: tool/temperature/fan commands deferred by decision D2 (motion-only v1)
_TOOL_M_CODES = {104: "set hotend temp", 105: "report temps",
                 106: "fan on/speed", 107: "fan off", 109: "set temp and wait"}
#: harmless housekeeping commands that many slicers emit; always skippable
_BENIGN_M_CODES = {84: "disable steppers"}


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
    active_tool: Optional[int] = None
    feed_mm_min: Optional[float] = None
    virtual_e: float = 0.0          # tracked so G92 E / relative E stay coherent
    last_motion_code: Optional[int] = None  # sticky G0/G1 for bare axis lines


@dataclass(frozen=True)
class Diagnostic:
    line_no: int
    message: str
    fatal: bool = False


@dataclass
class InterpretResult:
    actions: list[Action] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    skipped: Counter = field(default_factory=Counter)  # code -> count

    @property
    def errors(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.fatal]

    @property
    def warnings(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics if not d.fatal]

    @property
    def ok(self) -> bool:
        return not self.errors


class GcodeInterpreter:
    """Converts parsed G-code into actions, guided by a machine profile."""

    def __init__(self, profile: MachineProfile) -> None:
        self.profile = profile
        self.state = InterpreterState()
        self._warned_split = False
        self._warned_default_tool = False

    # ------------------------------------------------------------------ api
    def run(self, lines: Iterable[GcodeLine]) -> InterpretResult:
        result = InterpretResult()
        for line in lines:
            for err in line.errors:
                result.diagnostics.append(
                    Diagnostic(line.line_no, err, fatal=True))
            if line.is_empty:
                continue
            if self._step(line, result):
                break  # M2/M30: end of program
        return result

    # ------------------------------------------------------------- dispatch
    def _step(self, line: GcodeLine, result: InterpretResult) -> bool:
        """Consume one line; returns True when the program ends."""
        words = dict(line.words)
        st = self.state

        if "F" in words:
            feed = float(words.pop("F"))
            if feed <= 0:
                result.diagnostics.append(Diagnostic(
                    line.line_no, f"F{feed:g}: feed must be > 0", fatal=True))
            else:
                st.feed_mm_min = feed

        # T is a tool change only standalone; on G/M lines it is a parameter
        # (e.g. Cura's "M204 P500 R1000 T500" travel acceleration)
        if "T" in words and "G" not in words and "M" not in words:
            self._tool_select(int(words.pop("T")), line.line_no, result)

        if "G" in words:
            return self._g_command(int(words["G"]), line, result)
        if "M" in words:
            return self._m_command(int(words["M"]), line, result)

        # bare axis words: sticky motion mode (Marlin-style modal G0/G1)
        if any(a in words for a in "XYZE"):
            if st.last_motion_code is None:
                result.diagnostics.append(Diagnostic(
                    line.line_no,
                    "axis words with no active motion command (no prior G0/G1)",
                    fatal=True))
            else:
                self._motion(st.last_motion_code, line, result)
        return False

    # ------------------------------------------------------------ G commands
    def _g_command(self, code: int, line: GcodeLine,
                   result: InterpretResult) -> bool:
        st = self.state
        if code in (0, 1):
            st.last_motion_code = code
            self._motion(code, line, result)
        elif code in (2, 3):
            result.diagnostics.append(Diagnostic(
                line.line_no,
                f"G{code}: arcs are not supported in v1 (decision D4) — "
                f"re-slice with arcs disabled", fatal=True))
        elif code == 20:
            st.units = Units.INCH
            result.diagnostics.append(Diagnostic(
                line.line_no, "G20: inch units are not supported in v1",
                fatal=True))
        elif code == 21:
            st.units = Units.MM
        elif code == 28:
            axes = tuple(sorted(a for a in ("X", "Y", "Z") if a in line.words))
            logical: Optional[tuple[str, ...]]
            if axes:
                routed = []
                for a in axes:
                    name = self._route_axis(a, line.line_no, result)
                    if name:
                        routed.append(name)
                logical = tuple(routed)
                if not logical:  # every named axis was unmapped+skipped
                    return False
            else:
                logical = None
            result.actions.append(HomeAxes(line_no=line.line_no, axes=logical))
        elif code == 90:
            st.distance_mode = DistanceMode.ABSOLUTE
        elif code == 91:
            st.distance_mode = DistanceMode.RELATIVE
        elif code == 92:
            self._g92(line, result)
        else:
            self._unknown(f"G{code}", line, result)
        return False

    # ------------------------------------------------------------ M commands
    def _m_command(self, code: int, line: GcodeLine,
                   result: InterpretResult) -> bool:
        st = self.state
        if code in (0, 1):
            result.actions.append(PauseProgram(line_no=line.line_no,
                                               reason=f"M{code}"))
        elif code in (2, 30):
            result.actions.append(EndProgram(line_no=line.line_no))
            return True
        elif code == 82:
            st.extruder_mode = ExtruderMode.ABSOLUTE
        elif code == 83:
            st.extruder_mode = ExtruderMode.RELATIVE
        elif code in _TOOL_M_CODES:
            self._skip_tool_command(f"M{code}", _TOOL_M_CODES[code], line, result)
        elif code in _BENIGN_M_CODES:
            self._skip(f"M{code}", _BENIGN_M_CODES[code], line, result)
        else:
            self._unknown(f"M{code}", line, result)
        return False

    # -------------------------------------------------------------- motion
    def _motion(self, code: int, line: GcodeLine,
                result: InterpretResult) -> None:
        st = self.state
        words = line.words

        if "E" in words:
            if words["E"] is None:
                result.diagnostics.append(Diagnostic(
                    line.line_no, "E requires a value in a motion command",
                    fatal=True))
                return
            e = float(words["E"])
            # track the virtual extruder so absolute-E files stay coherent
            if st.extruder_mode is ExtruderMode.RELATIVE:
                st.virtual_e += e
            else:
                st.virtual_e = e
            self._require_tool(line.line_no, result)
            self._skip_tool_command(
                "E", "extrusion deferred (motion-only v1)", line, result)

        relative = st.distance_mode is DistanceMode.RELATIVE
        targets: dict[str, float] = {}
        for word in ("X", "Y", "Z"):
            if word not in words:
                continue
            if words[word] is None:  # bare flag is only valid on G28/M84
                result.diagnostics.append(Diagnostic(
                    line.line_no,
                    f"{word} requires a value in a motion command", fatal=True))
                return
            name = self._route_axis(word, line.line_no, result)
            if name is None:
                continue  # unmapped + skip policy: drop this component
            # X/Y route to themselves; Z routes to the tool's logical z-axis
            targets[word if word != "Z" else name] = self._to_machine(
                word, float(words[word]), relative)

        if not targets:
            return  # feed-only / E-only line: modal state already updated

        feed = self._feed_for(code, line, result)
        if feed is None:
            return

        xy = {a: v for a, v in targets.items() if a in ("X", "Y")}
        z = {a: v for a, v in targets.items() if a not in ("X", "Y")}
        if xy and z:
            if not self._warned_split:
                result.diagnostics.append(Diagnostic(
                    line.line_no,
                    "move touches Z and XY together; split Z-first "
                    "(3-axis interpolation deferred — printer PRD §2.2)"))
                self._warned_split = True
            result.actions.append(MoveLinear(line_no=line.line_no, axes=z,
                                             feed_mm_s=feed, relative=relative,
                                             rapid=code == 0))
            result.actions.append(MoveLinear(line_no=line.line_no, axes=xy,
                                             feed_mm_s=feed, relative=relative,
                                             rapid=code == 0))
        else:
            result.actions.append(MoveLinear(line_no=line.line_no, axes=targets,
                                             feed_mm_s=feed, relative=relative,
                                             rapid=code == 0))

    def _to_machine(self, word: str, value: float, relative: bool) -> float:
        """Slicer frame -> machine frame (FR-INT-08, profile workspace)."""
        w = self.profile.workspace
        if word in ("X", "Y"):
            return value if relative else value + w.xy_offset.get(word, 0.0)
        # Z: in "bed" mode the machine zero is at the TOP, so height above
        # the plane inverts: absolute -> plane_offset - z, relative -> -dz.
        # The plane offset belongs to the ACTIVE TOOL (schema v2) — _route_axis
        # has already resolved it before any Z reaches this point.
        if w.z_mode == "bed":
            tool = self.profile.tool(self.state.active_tool)  # set by _route_axis
            assert tool is not None and tool.plane_offset_mm is not None
            return -value if relative else tool.plane_offset_mm - value
        return value

    def _feed_for(self, code: int, line: GcodeLine,
                  result: InterpretResult) -> Optional[float]:
        """Modal F wins for G0 and G1 (RepRap dialect — slicers put the travel
        speed on the G0 line itself); the profile travel/default feeds are
        fallbacks for files that move before any F word."""
        st = self.state
        if st.feed_mm_min is not None:
            feed_mm_min = st.feed_mm_min
        elif code == 0 and self.profile.gcode.travel_feed_mm_min is not None:
            feed_mm_min = self.profile.gcode.travel_feed_mm_min
        else:
            feed_mm_min = self.profile.gcode.default_feed_mm_min
        return feed_mm_min * MM_PER_MIN_TO_MM_PER_S

    # ---------------------------------------------------------------- tools
    def _tool_select(self, index: int, line_no: int,
                     result: InterpretResult) -> None:
        if self.profile.tool(index) is None:
            result.diagnostics.append(Diagnostic(
                line_no,
                f"T{index}: tool is not configured in profile "
                f"{self.profile.profile_name!r}", fatal=True))
            return
        self.state.active_tool = index
        result.actions.append(SelectTool(line_no=line_no, tool_index=index))

    def _require_tool(self, line_no: int, result: InterpretResult) -> int:
        """Active tool, falling back to the profile default (FR-GC-023)."""
        if self.state.active_tool is None:
            self.state.active_tool = self.profile.gcode.default_tool
            if not self._warned_default_tool:
                result.diagnostics.append(Diagnostic(
                    line_no,
                    f"Z/E used before any Tn; assuming "
                    f"T{self.state.active_tool} (profile default)"))
                self._warned_default_tool = True
        return self.state.active_tool

    def _route_axis(self, word: str, line_no: int,
                    result: InterpretResult) -> Optional[str]:
        """Resolve an axis word to a mapped logical axis name (FR-INT-03)."""
        if word in ("X", "Y"):
            name = word
        else:  # Z: routed through the active tool
            tool_index = self._require_tool(line_no, result)
            tool = self.profile.tool(tool_index)
            assert tool is not None  # _tool_select validated configured tools
            name = tool.z_axis
            if name is None:
                self._missing_axis(f"tool T{tool_index} has no z_axis mapped",
                                   line_no, result)
                return None
        if not self.profile.has_axis(name):
            self._missing_axis(f"axis {name!r} is not a configured axis",
                               line_no, result)
            return None
        return name

    def _missing_axis(self, what: str, line_no: int,
                      result: InterpretResult) -> None:
        if self.profile.policies.missing_axis == POLICY_ERROR:
            result.diagnostics.append(Diagnostic(
                line_no, f"{what} (policies.missing_axis=error)", fatal=True))
        else:
            if not result.skipped[f"axis:{what}"]:
                result.diagnostics.append(Diagnostic(
                    line_no, f"{what}; dropping that motion component "
                             f"(policies.missing_axis=skip)"))
            result.skipped[f"axis:{what}"] += 1

    # ----------------------------------------------------------------- misc
    def _g92(self, line: GcodeLine, result: InterpretResult) -> None:
        words = line.words
        if words.get("E") is not None:
            self.state.virtual_e = float(words["E"])
        dropped = [a for a in ("X", "Y", "Z") if a in words]
        if dropped:
            self._skip("G92 " + "".join(dropped),
                       "position offsets are not supported in v1", line, result)
        # bare G92 (no words) re-zeros everything in RepRap; same warning
        if not dropped and "E" not in words:
            self._skip("G92", "position offsets are not supported in v1",
                       line, result)

    def _skip_tool_command(self, code: str, what: str, line: GcodeLine,
                           result: InterpretResult) -> None:
        if self.profile.policies.tool_commands == POLICY_SKIP:
            self._skip(code, f"{what} — tool control deferred (D2)", line, result)
        else:
            result.diagnostics.append(Diagnostic(
                line.line_no,
                f"{code}: tool control is not available in v1 "
                f"(policies.tool_commands=error)", fatal=True))

    def _unknown(self, code: str, line: GcodeLine,
                 result: InterpretResult) -> None:
        if self.profile.policies.unknown_command == POLICY_ERROR:
            result.diagnostics.append(Diagnostic(
                line.line_no, f"{code}: unknown/unsupported command "
                              f"(policies.unknown_command=error)", fatal=True))
        else:
            self._skip(code, "unknown/unsupported command", line, result)

    def _skip(self, code: str, reason: str, line: GcodeLine,
              result: InterpretResult) -> None:
        result.skipped[code] += 1
        result.actions.append(SkippedCommand(line_no=line.line_no, code=code,
                                             reason=reason))


def interpret(lines: Iterable[GcodeLine], profile: MachineProfile) -> InterpretResult:
    return GcodeInterpreter(profile).run(lines)

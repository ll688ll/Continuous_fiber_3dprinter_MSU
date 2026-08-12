"""G-code line parser (printer PRD FR-PARSE-01..03).

Stateless and word-level: each physical line becomes a ``GcodeLine`` with its
word map, comment, 1-based line number, and parse diagnostics. It never raises
on malformed input (NFR-REL-002) — problems are recorded per line and the
interpreter/preflight decide what is fatal. Dialect semantics (modal state,
routing) belong to the interpreter.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Iterable, Mapping

_WORD_RE = re.compile(
    r"([A-Za-z])\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
)

#: words whose value is a command code (usually integer)
_CODE_WORDS = frozenset("GMT")

#: letters allowed as bare flags without a number (Marlin-style "G28 X Y",
#: "M84 E"); their value is recorded as None
_FLAG_WORDS = frozenset("XYZE")


@dataclass(frozen=True)
class GcodeLine:
    raw: str
    words: Mapping[str, int | float | None]  # None = bare flag (e.g. G28 X)
    comment: str | None = None
    line_no: int = 0
    errors: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.words

    def word(self, letter: str, default=None):
        return self.words.get(letter, default)


def _split_comment(line: str) -> tuple[str, str | None]:
    """
    Returns (code, comment).
    Supports ';' comments and '( ... )' comments (simple, non-nested).
    """
    # ';' comment (everything after ';')
    if ";" in line:
        code, comment = line.split(";", 1)
        return code, comment.strip() or None

    # '( ... )' comment (first pair)
    start = line.find("(")
    end = line.find(")", start + 1) if start != -1 else -1
    if start != -1 and end != -1:
        comment = line[start + 1 : end].strip() or None
        code = (line[:start] + " " + line[end + 1 :]).strip()
        return code, comment

    return line, None


def parse_line(line: str, line_no: int = 0) -> GcodeLine:
    """Parse a single G-code line into (word -> numeric value) + diagnostics."""
    raw = line.rstrip("\n")
    code, comment = _split_comment(raw)
    code = code.strip()

    words: dict[str, int | float] = {}
    errors: list[str] = []
    matched_spans: list[tuple[int, int]] = []

    for m in _WORD_RE.finditer(code):
        letter_u = m.group(1).upper()
        number_str = m.group(2)
        matched_spans.append(m.span())
        if letter_u in words:
            errors.append(f"duplicate word {letter_u!r}")
            continue
        value = float(number_str)
        if letter_u in _CODE_WORDS:
            if value != int(value):
                errors.append(f"{letter_u}{number_str}: command code must be an integer")
                words[letter_u] = value
            else:
                words[letter_u] = int(value)
        else:
            words[letter_u] = value

    # anything not consumed by a letter+number word is either a bare axis
    # flag (Marlin "G28 X Y") or garbage
    leftover = []
    pos = 0
    for start, end in matched_spans:
        leftover.append(code[pos:start])
        pos = end
    leftover.append(code[pos:])
    for token in "".join(leftover).split():
        upper = token.upper()
        if upper in _FLAG_WORDS:
            if upper in words:
                errors.append(f"duplicate word {upper!r}")
            else:
                words[upper] = None
        else:
            errors.append(f"unrecognized text: {token!r}")

    return GcodeLine(raw=raw, words=words, comment=comment, line_no=line_no,
                     errors=tuple(errors))


def parse_lines(lines: Iterable[str], start_line_no: int = 1) -> list[GcodeLine]:
    return [parse_line(line, line_no=i)
            for i, line in enumerate(lines, start=start_line_no)]


def parse_text(text: str) -> list[GcodeLine]:
    return parse_lines(text.splitlines())


def parse_file(path: str | Path) -> list[GcodeLine]:
    """Parse a G-code file (FR-GC-001); tolerant of encoding oddities."""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    return parse_text(text)

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Mapping


_WORD_RE = re.compile(
    r"([A-Za-z])\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
)


@dataclass(frozen=True)
class GcodeLine:
    raw: str
    words: Mapping[str, int | float]
    comment: str | None = None


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


def parse_line(line: str) -> GcodeLine:
    """
    Parse a single G-code line into (word -> numeric value).

    Notes:
    - This is intentionally small and deterministic so it can be unit-tested
      independently of hardware and UI.
    - It does not implement full dialect semantics; that belongs in the interpreter.
    """
    raw = line.rstrip("\n")
    code, comment = _split_comment(raw)
    code = code.strip()

    words: dict[str, int | float] = {}
    for letter, number_str in _WORD_RE.findall(code):
        letter_u = letter.upper()
        # G/M/T are usually integer codes.
        if letter_u in {"G", "M", "T"}:
            try:
                words[letter_u] = int(float(number_str))
            except ValueError:
                # Fall back to float; interpreter can flag this later.
                words[letter_u] = float(number_str)
        else:
            words[letter_u] = float(number_str)

    return GcodeLine(raw=raw, words=words, comment=comment)


def parse_lines(lines: list[str]) -> list[GcodeLine]:
    return [parse_line(line) for line in lines]



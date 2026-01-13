from __future__ import annotations

from printer_msu.core.gcode.parser import parse_line


def test_parse_line_basic_words_and_comment() -> None:
    line = "G1 X10.5 Y-2.0 E0.25 F1500 ; hello"
    parsed = parse_line(line)

    assert parsed.words["G"] == 1
    assert parsed.words["X"] == 10.5
    assert parsed.words["Y"] == -2.0
    assert parsed.words["E"] == 0.25
    assert parsed.words["F"] == 1500.0
    assert parsed.comment == "hello"



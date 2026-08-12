from __future__ import annotations

from printer_msu.core.gcode.parser import parse_file, parse_line, parse_text


def test_parse_line_basic_words_and_comment() -> None:
    line = "G1 X10.5 Y-2.0 E0.25 F1500 ; hello"
    parsed = parse_line(line)

    assert parsed.words["G"] == 1
    assert parsed.words["X"] == 10.5
    assert parsed.words["Y"] == -2.0
    assert parsed.words["E"] == 0.25
    assert parsed.words["F"] == 1500.0
    assert parsed.comment == "hello"
    assert parsed.errors == ()


def test_parse_line_no_spaces_and_lowercase() -> None:
    parsed = parse_line("g1x10y5")
    assert parsed.words == {"G": 1, "X": 10.0, "Y": 5.0}


def test_parse_line_paren_comment() -> None:
    parsed = parse_line("G28 (home all) X")
    assert parsed.words["G"] == 28
    assert parsed.comment == "home all"
    assert parsed.words["X"] is None  # bare axis flag
    assert parsed.errors == ()


def test_parse_line_bare_axis_flags() -> None:
    parsed = parse_line("G28 X Y")
    assert parsed.words == {"G": 28, "X": None, "Y": None}
    assert parsed.errors == ()


def test_parse_line_bare_non_axis_letter_is_junk() -> None:
    parsed = parse_line("G28 Q")
    assert any("unrecognized text: 'Q'" in e for e in parsed.errors)


def test_parse_line_duplicate_word_flagged() -> None:
    parsed = parse_line("G1 X10 X20")
    assert parsed.words["X"] == 10.0  # first occurrence wins
    assert any("duplicate word 'X'" in e for e in parsed.errors)


def test_parse_line_non_integer_code_flagged() -> None:
    parsed = parse_line("G1.5 X10")
    assert any("must be an integer" in e for e in parsed.errors)


def test_parse_line_junk_flagged() -> None:
    parsed = parse_line("G1 X10 hello")
    assert any("unrecognized text" in e for e in parsed.errors)


def test_parse_line_empty_and_comment_only() -> None:
    assert parse_line("").is_empty
    assert parse_line("; just a comment").is_empty
    assert parse_line("   ").is_empty


def test_parse_text_line_numbers() -> None:
    lines = parse_text("G28\nG1 X1\n\nG1 Y2")
    assert [l.line_no for l in lines] == [1, 2, 3, 4]
    assert lines[2].is_empty


def test_parse_file_roundtrip(tmp_path) -> None:
    p = tmp_path / "t.gcode"
    p.write_text("G21\nG90\nG1 X5 Y5 F600\n", encoding="utf-8")
    lines = parse_file(p)
    assert len(lines) == 3
    assert lines[2].words["X"] == 5.0

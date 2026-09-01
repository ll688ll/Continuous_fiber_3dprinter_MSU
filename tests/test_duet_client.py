"""DuetClient against the ScriptedDuetBoard fake (no network, no hardware)."""

from __future__ import annotations

import pytest

from printer_msu.controllers.motion.duet.client import (
    BUFF_TOTAL,
    DuetClient,
    DuetConnectionError,
    DuetError,
    DuetLineTooLong,
)

from duet_testlib import ScriptedDuetBoard


def _client(board: ScriptedDuetBoard, **kwargs) -> DuetClient:
    return DuetClient("fake-board", opener=board, **kwargs)


def test_connect_ok_and_refused():
    board = ScriptedDuetBoard()
    client = _client(board)
    client.connect()
    assert board.connect_calls == 1

    board.fail_connect = True
    with pytest.raises(DuetConnectionError, match="refused"):
        client.connect()


def test_reads_retry_on_transient_failures():
    board = ScriptedDuetBoard()
    client = _client(board)
    client.RETRY_DELAY_S = 0.0
    board.transient_failures = 2  # fewer than READ_RETRIES
    assert client.model("state.status") == "idle"


def test_reads_fail_after_retries_exhausted():
    board = ScriptedDuetBoard()
    client = _client(board)
    client.RETRY_DELAY_S = 0.0
    board.transient_failures = 10
    with pytest.raises(DuetConnectionError):
        client.model("state.status")


def test_send_refuses_oversize_line():
    client = _client(ScriptedDuetBoard())
    with pytest.raises(DuetLineTooLong):
        client.send("G1 X" + "9" * BUFF_TOTAL)


def test_send_refuses_payload_larger_than_free_space():
    """The client-side guard for the silent-drop trap."""
    board = ScriptedDuetBoard()
    client = _client(board)
    client.avail = 10
    with pytest.raises(DuetError, match="wait_for_space"):
        client.send("G1 X100 F600 ; way more than ten bytes")
    assert board.command_log == []  # nothing reached the board


def test_board_fake_reproduces_the_silent_drop():
    """Prove the fake models the real failure mode: a raw oversized request
    loses bytes with NO error — this is what the client guard protects
    against, and why batching is by bytes forever."""
    board = ScriptedDuetBoard()
    board.drain_per_request = 0
    board.avail = 30
    big = "G28\n" + "G1 X123.456 F600\n" * 5
    board(f"http://fake/rr_gcode?gcode={big}", None, 5.0)  # raw, no client
    assert board.overflow_dropped > 0
    assert len(board.command_log) < 6  # tail commands simply vanished


def test_wait_for_space_with_slow_drain():
    board = ScriptedDuetBoard()
    board.drain_per_request = 40
    client = _client(board)
    client.avail = 0
    board.avail = 0
    assert client.wait_for_space(100, deadline_s=5.0)
    assert client.avail >= 100


def test_upload_crc_verified_and_stored():
    board = ScriptedDuetBoard()
    client = _client(board)
    client.upload("/sys/msu_machine.g", b"M584 X0\n")
    assert board.files["/sys/msu_machine.g"] == b"M584 X0\n"


def test_upload_crc_mismatch_raises():
    class CorruptingBoard(ScriptedDuetBoard):
        def _upload(self, name, crc_hex, data):
            return super()._upload(name, crc_hex, data[:-1])  # lose a byte

    client = _client(CorruptingBoard())
    with pytest.raises(DuetError, match="rr_upload"):
        client.upload("/sys/msu_machine.g", b"M584 X0\n")


def test_send_never_retries_gcode():
    """A motion payload must not be silently re-sent (duplicated move)."""
    board = ScriptedDuetBoard()
    client = _client(board)
    client.RETRY_DELAY_S = 0.0
    board.transient_failures = 1
    with pytest.raises(DuetConnectionError):
        client.send("G1 X5 F600")
    assert board.command_log == []  # exactly zero executions, not two


def test_download_round_trips_upload():
    board = ScriptedDuetBoard()
    client = DuetClient("fake", opener=board)
    client.connect()
    payload = b"M92 X142.857\nM208 X987.448 S0\n"
    client.upload("/sys/msu_machine.g", payload)
    assert client.download("/sys/msu_machine.g") == payload


def test_download_missing_file_raises():
    board = ScriptedDuetBoard()
    client = DuetClient("fake", opener=board)
    client.connect()
    with pytest.raises(DuetConnectionError):
        client.download("/sys/nope.g")

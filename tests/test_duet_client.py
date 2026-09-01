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


# --------------------------------- keep-alive transport (WinError 10048 fix)
class _StubResponse:
    def __init__(self, body=b'{"err":0}'):
        self.status = 200
        self._body = body

    def read(self):
        return self._body


class _StubConn:
    """Stands in for http.client.HTTPConnection."""

    instances = []

    def __init__(self, netloc, timeout=None):
        self.netloc = netloc
        self.timeout = timeout
        self.sock = None
        self.requests = []
        self.fail_next = 0
        _StubConn.instances.append(self)

    def request(self, method, target, body=None):
        if self.fail_next:
            self.fail_next -= 1
            raise ConnectionResetError("stale keep-alive")
        self.sock = object.__new__(_FakeSock)
        self.requests.append((method, target))

    def getresponse(self):
        return _StubResponse()

    def close(self):
        self.sock = None


class _FakeSock:
    def settimeout(self, t):
        pass


@pytest.fixture()
def stub_transport(monkeypatch):
    from printer_msu.controllers.motion.duet import client as client_mod

    _StubConn.instances = []
    monkeypatch.setattr(client_mod.http.client, "HTTPConnection", _StubConn)
    return client_mod._KeepAliveTransport()


def test_transport_reuses_one_connection(stub_transport):
    for _ in range(5):
        stub_transport("http://board/rr_model?key=state.status", None, 5.0)
    assert len(_StubConn.instances) == 1
    assert len(_StubConn.instances[0].requests) == 5


def test_transport_retries_once_on_stale_reused_socket(stub_transport):
    stub_transport("http://board/rr_reply", None, 5.0)  # establishes reuse
    _StubConn.instances[0].fail_next = 1
    body = stub_transport("http://board/rr_reply", None, 5.0)
    assert body == b'{"err":0}'
    assert len(_StubConn.instances) == 2  # reconnected on a fresh conn


def test_transport_does_not_retry_fresh_connection_failures(stub_transport):
    # a failure on the FIRST use of a connection is not a stale keep-alive:
    # never blind-resend (rr_gcode could double-execute)
    _StubConn.instances.clear()

    class _AlwaysFail(_StubConn):
        def request(self, method, target, body=None):
            raise ConnectionRefusedError("board down")

    from printer_msu.controllers.motion.duet import client as client_mod
    transport = client_mod._KeepAliveTransport()
    client_mod.http.client.HTTPConnection = _AlwaysFail
    try:
        with pytest.raises(ConnectionRefusedError):
            transport("http://board/rr_gcode?gcode=G1", None, 5.0)
    finally:
        client_mod.http.client.HTTPConnection = _StubConn


def test_transport_closes_when_server_says_connection_close(stub_transport):
    _StubResponse_orig_read = _StubResponse.read

    class _CloseResponse(_StubResponse):
        will_close = True

    _StubConn.instances.clear()
    conn_holder = _StubConn.instances

    def getresponse_close(self):
        return _CloseResponse()

    orig = _StubConn.getresponse
    _StubConn.getresponse = getresponse_close
    try:
        stub_transport("http://board/rr_reply", None, 5.0)
        stub_transport("http://board/rr_reply", None, 5.0)
    finally:
        _StubConn.getresponse = orig
    # two fresh connections, but ZERO failed attempts in between
    assert len(conn_holder) == 2
    assert all(len(c.requests) == 1 for c in conn_holder)


def test_transport_retries_addr_in_use_even_on_fresh_connection(stub_transport):
    """WinError 10048 = local port collision at CONNECT time; nothing was
    sent, so it is retried even for rr_gcode (job-20260831-230939 fix)."""
    _StubConn.instances.clear()

    class _CollidingConn(_StubConn):
        collisions = 2

        def request(self, method, target, body=None):
            if _CollidingConn.collisions:
                _CollidingConn.collisions -= 1
                err = OSError("port collision")
                err.winerror = 10048
                err.errno = None
                raise err
            super().request(method, target, body)

    from printer_msu.controllers.motion.duet import client as client_mod
    client_mod.http.client.HTTPConnection = _CollidingConn
    try:
        transport = client_mod._KeepAliveTransport()
        body = transport("http://board/rr_gcode?gcode=G1 X5", None, 5.0)
        assert body == b'{"err":0}'
        assert _CollidingConn.collisions == 0
    finally:
        client_mod.http.client.HTTPConnection = _StubConn

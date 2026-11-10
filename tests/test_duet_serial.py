"""USB serial transport: DuetSerialClient, the adapter and both execution
modes over it, profile/CLI/factory wiring. Runs against FakeUsbDuet (the
same scripted board as the HTTP tests, behind RRF's serial dialect)."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from types import SimpleNamespace

import pytest

from printer_msu.config.profile import (
    ProfileError,
    profile_from_dict,
    with_duet_transport,
)
from printer_msu.controllers.motion.duet import make_duet_client
from printer_msu.controllers.motion.duet import serial_client as sc
from printer_msu.controllers.motion.duet.adapter import DuetMotionAdapter
from printer_msu.controllers.motion.duet.client import (
    DuetClient,
    DuetConnectionError,
    DuetError,
    DuetLineTooLong,
)
from printer_msu.controllers.motion.duet.serial_client import (
    DuetSerialClient,
    normalize_upload,
)
from printer_msu.core.gcode.interpreter import interpret
from printer_msu.core.gcode.parser import parse_text
from printer_msu.core.model.print_job import PrintJobState
from printer_msu.services.print_service import PrintJobRunner, wait_for_state

from duet_testlib import FakeUsbDuet, ScriptedDuetBoard
from printer_testlib import DUET_PROFILE_DATA, make_duet_profile

GCODE = "G28\nG1 X10 Y20 F600\nG1 X30 Y40 F600\n"


def _client(fake: FakeUsbDuet, **kw) -> DuetSerialClient:
    client = DuetSerialClient(port=kw.pop("port", "COM7"),
                              timeout_s=kw.pop("timeout_s", 2.0),
                              link_factory=fake, **kw)
    client.SETTLE_S = 0.0
    client.RETRY_DELAY_S = 0.0
    client.FIRST_FILE_ACK_S = 0.2
    return client


def _connected(fake: FakeUsbDuet | None = None, **kw):
    fake = fake or FakeUsbDuet()
    client = _client(fake, **kw)
    client.connect()
    return fake, client


def _adapter(fake: FakeUsbDuet | None = None):
    fake, client = _connected(fake)
    profile = make_duet_profile()
    adapter = DuetMotionAdapter(client, profile.axes_on("duet"))
    adapter.POLL_S = 0.01
    adapter.IDLE_GRACE_S = 0.05
    adapter.connect()
    return fake, client, adapter, profile


# -------------------------------------------------------------- the client
def test_connect_syncs_with_m115_and_identifies_rrf():
    fake, client = _connected()
    assert fake.opened == ["COM7"]
    assert "RepRapFirmware" in client.firmware
    assert client.base == "usb:COM7"


def test_connect_refuses_a_device_that_is_not_rrf():
    fake = FakeUsbDuet()
    fake.firmware_name = "FIRMWARE_NAME: Marlin 2.1"
    with pytest.raises(DuetConnectionError, match="not RepRapFirmware"):
        _client(fake).connect()


def test_connect_discards_stale_lines_already_in_the_port():
    """Leftover 'ok's from an earlier session must not ack our commands."""
    fake = FakeUsbDuet()
    client = _client(fake)
    real_factory = fake.__call__

    def factory(port, baud):
        link = real_factory(port, baud)
        fake.emit("ok", "ok", "Warning: stale message", "ok")
        return link

    client._factory = factory
    client.connect()
    assert client.model("state.status") == "idle"


def test_model_reads_the_object_model_via_m409():
    fake, client = _connected()
    axes = client.model("move.axes")
    assert [a["letter"] for a in axes] == ["X", "Y", "Z"]
    assert 'M409 K"move.axes"' in fake.received


def test_send_is_one_line_per_ok_and_collects_replies():
    fake, client = _connected()
    client.send("G90\nG1 X10 F600")  # X unhomed -> refused, like the board
    assert fake.received[-2:] == ["G90", "G1 X10 F600"]
    assert "insufficient axes homed" in client.reply()
    assert client.reply() == ""  # drained


def test_send_refuses_a_line_longer_than_the_board_buffer():
    _, client = _connected()
    with pytest.raises(DuetLineTooLong):
        client.send("G1 X" + "9" * 300)


def test_wait_for_space_is_always_true_over_usb():
    _, client = _connected()
    assert client.wait_for_space(10_000) is True


def test_missed_ok_times_out_then_the_next_command_resyncs():
    fake, client = _connected(timeout_s=0.3)
    fake.drop_ok.add("G90")
    with pytest.raises(DuetConnectionError, match="no 'ok'"):
        client.send("G90")
    # desynchronised: the next read must resync (M115) and still be right
    assert client.model("state.status") == "idle"
    assert fake.received[-2:] == ["M115", 'M409 K"state.status"']


def test_reads_reopen_a_port_that_vanished():
    fake, client = _connected()
    fake.link.dead = True               # USB dropped and re-enumerated
    assert client.model("state.status") == "idle"
    assert fake.opened == ["COM7", "COM7"]


def test_a_failed_link_is_closed_not_just_forgotten():
    """Windows COM ports are exclusive: a leaked handle blocks the reopen."""
    fake, client = _connected()
    dead = fake.link
    dead.dead = True
    with pytest.raises(DuetConnectionError):
        client.send("G90")
    assert dead.closed


def test_connect_releases_the_port_when_the_device_is_not_rrf():
    fake = FakeUsbDuet()
    fake.firmware_name = "FIRMWARE_NAME: Marlin 2.1"
    with pytest.raises(DuetConnectionError):
        _client(fake).connect()
    assert fake.link.closed


def test_motion_is_never_resent_after_a_link_failure():
    fake, client = _connected()
    fake.link.dead = True
    with pytest.raises(DuetConnectionError):
        client.send("G1 X1 F600")
    assert "G1 X1 F600" not in fake.received


def test_emergency_write_bypasses_a_held_command_lock():
    """A G28 holds the lock for minutes over USB; M112 must not wait."""
    fake, client = _connected()
    held, release = threading.Event(), threading.Event()

    def long_command():
        with client._lock:
            held.set()
            release.wait(5.0)

    worker = threading.Thread(target=long_command)
    worker.start()
    assert held.wait(2.0)
    started = time.monotonic()
    client.send_emergency("M112")
    assert time.monotonic() - started < 0.5
    assert "M112" in fake.received and fake.board.halted
    release.set()
    worker.join(2.0)


def test_m999_closes_the_dead_port_and_connect_reopens():
    fake, client = _connected()
    client.send_emergency("M999")
    assert client._link is None
    client.connect()
    assert fake.opened == ["COM7", "COM7"]


def test_download_is_refused_over_usb():
    _, client = _connected()
    with pytest.raises(DuetError, match="Ethernet"):
        client.download("/sys/config.g")


def test_filelist_via_m20():
    fake, client = _connected()
    fake.board.files["/sys/homeall.g"] = b"G28\n"
    entries = client.filelist("0:/sys")
    assert {"type": "f", "name": "homeall.g"} in entries
    assert {"type": "d", "name": "msu_jobs"} in entries


# ----------------------------------------------------------------- upload
def test_normalize_upload_strips_comments_outside_quotes():
    data = (b"; header\nG1 X1 ; move\n\nM118 S\"a;b\"  \n"
            b"   ; indented comment\nG90\n")
    assert normalize_upload(data) == b"G1 X1\nM118 S\"a;b\"\nG90\n"
    assert normalize_upload(b"; only comments\n") == b""


def test_upload_lockstep_writes_exactly_the_normalized_bytes():
    fake, client = _connected()
    body = b"; generated\nM92 X142.857 ; scale\nM208 X0:987.448\n"
    client.upload("/sys/msu_machine.g", body)
    assert fake.board.files["/sys/msu_machine.g"] == normalize_upload(body)
    # M28 ... lines ... M29, then the M115 resync and the M38 check
    i = fake.received.index('M28 "/sys/msu_machine.g"')
    assert fake.received[i + 1:i + 6] == [
        "M92 X142.857", "M208 X0:987.448", "M29", "M115",
        'M38 "/sys/msu_machine.g"']


def test_upload_pipelines_when_the_board_does_not_ack_file_lines():
    fake = FakeUsbDuet()
    fake.ack_file_lines = False
    _, client = _connected(fake)
    body = "".join(f"G1 X{i} F600\n" for i in range(100)).encode()
    client.upload("/gcodes/msu_jobs/job.g", body)
    assert fake.board.files["/gcodes/msu_jobs/job.g"] == body
    # still usable afterwards: the sync discarded nothing it should not
    assert client.model("state.status") == "idle"


def test_upload_sends_no_file_line_unless_m28_is_confirmed():
    """Otherwise the file's G-code would EXECUTE on the machine."""
    fake = FakeUsbDuet()
    fake.refuse_m28 = True
    _, client = _connected(fake)
    with pytest.raises(DuetError, match="nothing was sent"):
        client.upload("/gcodes/msu_jobs/job.g", b"G1 X50 F600\n")
    assert "G1 X50 F600" not in fake.received


def test_upload_detects_a_sha1_mismatch():
    fake = FakeUsbDuet()
    fake.corrupt_upload = True
    _, client = _connected(fake)
    with pytest.raises(DuetError, match="SHA-1 mismatch"):
        client.upload("/sys/msu_machine.g", b"M92 X142.857\n")


# --------------------------------------------------------- port discovery
def _fake_ports(monkeypatch, ports):
    monkeypatch.setattr(sc, "list_serial_ports", lambda: ports)


def test_auto_port_finds_the_single_duet(monkeypatch):
    _fake_ports(monkeypatch, [("COM1", "Communications Port", False),
                              ("COM9", "Duet 3", True)])
    fake = FakeUsbDuet()
    client = _client(fake, port="auto")
    client.connect()
    assert fake.opened == ["COM9"] and client.port == "COM9"


def test_auto_port_explains_no_duet_and_several_duets(monkeypatch):
    _fake_ports(monkeypatch, [("COM1", "Communications Port", False)])
    with pytest.raises(DuetConnectionError, match="no Duet found.*COM1"):
        sc.find_duet_port()
    _fake_ports(monkeypatch, [("COM3", "Duet 3", True), ("COM4", "Duet 3", True)])
    with pytest.raises(DuetConnectionError, match="more than one"):
        sc.find_duet_port()


# -------------------------------------------------- adapter + execution
def test_adapter_over_usb_connects_homes_jogs_and_refuses_envelope():
    fake, _client_, adapter, _ = _adapter()
    assert adapter.drift_findings == []
    adapter.home()
    assert adapter.homed_axes() == {"X", "Y", "Z_T0"}
    adapter.move_absolute({"X": 25.0}, feed=50.0)
    assert fake.board.axes["X"]["machinePosition"] == pytest.approx(25.0)
    with pytest.raises(DuetError, match="soft limits"):
        adapter.move_absolute({"X": 2000.0}, feed=50.0)


def test_adapter_clear_estop_reconnects_after_the_usb_reset():
    fake, _client_, adapter, _ = _adapter()
    adapter.home()
    opens = len(fake.opened)
    adapter.estop()
    assert fake.board.halted
    adapter.RECONNECT_TIMEOUT_S = 5.0
    adapter.clear_estop()
    assert len(fake.opened) == opens + 1  # the port was reopened
    assert adapter.homed_axes() == set()  # RRF datums do not survive reset


def test_adapter_disconnect_releases_the_port():
    fake, client, adapter, _ = _adapter()
    adapter.disconnect()
    assert fake.link.closed and client._link is None


def _runner(adapter, profile, tmp_path, execution):
    result = interpret(parse_text(GCODE), profile)
    runner = PrintJobRunner(profile, adapter, result, "part.gcode",
                            job_id="job-usb", report_path=tmp_path / "job.json",
                            execution=execution)
    runner._executor.POLL_S = 0.01
    if hasattr(runner._executor, "START_VERIFY_S"):
        runner._executor.START_VERIFY_S = 2.0
    return runner


def test_preload_over_usb_uploads_comment_free_program_and_completes(tmp_path):
    fake, _client_, adapter, profile = _adapter()
    runner = _runner(adapter, profile, tmp_path, "preload")
    runner.start()
    assert wait_for_state(runner, PrintJobState.COMPLETED, timeout=15.0), \
        runner.error_message
    assert runner.join(5.0)
    data = fake.board.files["/gcodes/msu_jobs/job-usb.g"]
    assert b";" not in data                               # comments stripped
    assert any(c.startswith("M32") for c in fake.board.command_log)
    assert fake.board.axes["X"]["machinePosition"] == pytest.approx(60.0)
    report = json.loads((tmp_path / "job.json").read_text())
    assert report["execution"] == "preload"
    assert report["board_duration_s"] is not None


def test_stream_over_usb_feeds_every_move_and_completes(tmp_path):
    fake, _client_, adapter, profile = _adapter()
    runner = _runner(adapter, profile, tmp_path, "stream")
    runner.start()
    assert wait_for_state(runner, PrintJobState.COMPLETED, timeout=15.0), \
        runner.error_message
    moves = [c for c in fake.board.command_log if c.startswith("G1")]
    assert len(moves) >= 2
    assert fake.board.axes["Y"]["machinePosition"] == pytest.approx(70.0)
    assert not any(line.startswith(";") for line in fake.received)


# ------------------------------------------------- profile, factory, CLI
def _usb_data(**conn):
    data = json.loads(json.dumps(DUET_PROFILE_DATA))
    data["controllers"][0]["connection"] = {"transport": "usb", **conn}
    return data


def test_profile_usb_transport_defaults_and_no_host_needed():
    profile = profile_from_dict(_usb_data())
    duet = profile.controller("duet")
    assert duet.duet_transport == "usb" and duet.duet_port == "auto"
    assert duet.connection["serial_timeout_s"] == 20.0
    assert "host" not in duet.connection


def test_profile_rejects_unknown_transport_and_bad_port():
    with pytest.raises(ProfileError, match="transport"):
        profile_from_dict(_usb_data(transport="bluetooth"))
    with pytest.raises(ProfileError, match="port"):
        profile_from_dict(_usb_data(port=""))


def test_existing_http_profiles_are_unchanged():
    duet = make_duet_profile().controller("duet")
    assert duet.duet_transport == "http"
    assert "transport" not in duet.connection and "port" not in duet.connection


def test_with_duet_transport_switches_both_ways():
    http = make_duet_profile()
    usb = with_duet_transport(http, "usb", "COM5")
    assert usb.controller("duet").duet_port == "COM5"
    assert usb.controller("duet").duet_host == "192.168.0.40"  # kept
    assert with_duet_transport(usb, "http").controller("duet") \
        .duet_transport == "http"
    with pytest.raises(ProfileError, match="host"):
        with_duet_transport(profile_from_dict(_usb_data()), "http")


def test_factory_picks_the_client_for_the_transport():
    assert isinstance(make_duet_client({"host": "192.168.0.40"}), DuetClient)
    client = make_duet_client({"transport": "usb", "port": "COM3",
                               "serial_timeout_s": 7.0})
    assert isinstance(client, DuetSerialClient)
    assert client.port_setting == "COM3" and client._timeout == 7.0


def test_cli_port_implies_usb_and_override_is_in_memory_only(tmp_path):
    from printer_msu import cli

    path = tmp_path / "machine.json"
    path.write_text(json.dumps(DUET_PROFILE_DATA))
    args = SimpleNamespace(profile=str(path), mode=None, transport=None,
                           port="COM4")
    profile = cli._load_profile(args)
    assert profile.controller("duet").duet_transport == "usb"
    assert profile.controller("duet").duet_port == "COM4"
    assert "transport" not in json.loads(path.read_text())["controllers"][0][
        "connection"]


def test_cli_ports_lists_and_marks_the_duet(monkeypatch, capsys):
    from printer_msu import cli

    _fake_ports(monkeypatch, [("COM1", "Communications Port", False),
                              ("COM9", "Duet 3", True)])
    assert cli.main(["ports"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "COM9" in out and "<-- Duet" in out and "will use COM9" in out

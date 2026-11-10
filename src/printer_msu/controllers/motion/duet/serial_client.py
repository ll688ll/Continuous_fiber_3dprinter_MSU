"""USB serial client for RepRapFirmware: the HTTP client's interface over a COM port.

The Duet 3 enumerates as a USB CDC device (VID 0x1D50, PID 0x60EC/0x60EE),
so the baud rate is ignored. RRF answers every command with zero or more
reply lines followed by ``ok`` (proven by ``firmware/duet3_6xd/duet_send.py``
during commissioning). ``DuetSerialClient`` exposes the same methods as
``DuetClient`` so the adapter, both execution modes, the tool controller and
Setup's Apply run unchanged on either link:

=================  ==========================================  ================
DuetClient (HTTP)  USB serial equivalent                        notes
=================  ==========================================  ================
rr_connect         open port + ``M115`` sync                    checks it is RRF
rr_model?key=K     ``M409 K"K"``                                same JSON shape
rr_gcode           one line at a time, each waits for ``ok``    ``ok`` IS the flow control
rr_reply           reply lines collected while waiting          drained by reply()
rr_upload          ``M28 "path"`` / lines / ``M29``             ``M38`` SHA-1 verify
rr_download        none                                         raises DuetError
rr_filelist        ``M20 S2 P"dir"``
=================  ==========================================  ================

Rules carried over from the HTTP client and the lab:

* motion is NEVER re-sent: a write that fails raises, it is not retried;
* reads (``model``) are idempotent and retried, reopening the port if the
  USB device re-enumerated (it did, repeatedly, on the lab workstation);
* ``send_emergency`` writes M112/M999 WITHOUT taking the command lock, so an
  e-stop never queues behind a long command such as ``G28`` (RRF acts on
  M112 from USB immediately, even while that channel is busy);
* if an ``ok`` is ever missed (timeout), the stream is desynchronised: the
  next command first resynchronises with ``M115`` and discards stale lines;
* ``M28`` never sends a single file line until the board has confirmed it is
  writing a file. Otherwise the file's G-code would EXECUTE.

Uploads over USB are comment-stripped (``normalize_upload``): RRF's line
parser may drop comments while writing a file, and the SHA-1 check compares
against exactly what the board should hold. Comments do nothing on the board.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from typing import Any, Callable, Optional, Protocol

from .client import BUFF_TOTAL, DuetConnectionError, DuetError, DuetLineTooLong

logger = logging.getLogger("printer_msu.duet.usb")

#: Duet 3 USB identities (RRF firmware / bootloader-less app image)
DUET_USB_IDS = frozenset({(0x1D50, 0x60EC), (0x1D50, 0x60EE)})
AUTO_PORT = "auto"


class SerialLink(Protocol):
    """The slice of ``serial.Serial`` the client uses (test seam)."""

    def write(self, data: bytes) -> Optional[int]: ...
    def readline(self) -> bytes: ...
    def close(self) -> None: ...


#: (port, baud) -> open SerialLink
LinkFactory = Callable[[str, int], SerialLink]


# ------------------------------------------------------------------ ports
def _require_pyserial():
    try:
        import serial  # noqa: F401
        from serial.tools import list_ports
    except ImportError as exc:  # pragma: no cover - environment problem
        raise DuetConnectionError(
            "USB transport needs pyserial: pip install \"printer_msu[usb]\" "
            "(the Windows bundle already includes it)") from exc
    return list_ports


def list_serial_ports() -> list[tuple[str, str, bool]]:
    """``(device, description, is_duet)`` for every serial port present."""
    list_ports = _require_pyserial()
    return [(p.device, p.description or "", (p.vid, p.pid) in DUET_USB_IDS)
            for p in sorted(list_ports.comports(), key=lambda p: p.device)]


def find_duet_port() -> str:
    """The single Duet on USB, or a DuetConnectionError explaining why not."""
    ports = list_serial_ports()
    duets = [dev for dev, _desc, is_duet in ports if is_duet]
    if len(duets) == 1:
        return duets[0]
    if not duets:
        seen = ", ".join(f"{dev} ({desc})" for dev, desc, _ in ports) or "none"
        raise DuetConnectionError(
            f"no Duet found on USB (ports present: {seen}). Check the cable, "
            f"close Cura (it holds COM ports open), or set connection.port")
    raise DuetConnectionError(
        f"more than one Duet on USB ({', '.join(duets)}); set connection.port")


def _pyserial_factory(port: str, baud: int) -> SerialLink:
    _require_pyserial()
    import serial

    try:
        return serial.Serial(port, baud, timeout=DuetSerialClient.READ_POLL_S,
                             write_timeout=DuetSerialClient.WRITE_TIMEOUT_S)
    except (serial.SerialException, OSError) as exc:
        hint = (" (another program holds it: close Cura, DWC's serial "
                "console, or a second printer-msu)"
                if "denied" in str(exc).lower() else "")
        raise DuetConnectionError(f"cannot open {port}: {exc}{hint}") from exc


# ----------------------------------------------------------- upload text
def _strip_comment(line: str) -> str:
    """Drop a ``;`` comment that is not inside a quoted string."""
    in_quotes = False
    for i, ch in enumerate(line):
        if ch == '"':
            in_quotes = not in_quotes
        elif ch == ";" and not in_quotes:
            return line[:i]
    return line


def normalize_upload(data: bytes) -> bytes:
    """The exact bytes a USB upload writes: comments stripped, trailing
    whitespace removed, blank lines dropped, LF endings."""
    text = data.decode("ascii", "replace")
    lines = []
    for raw in text.splitlines():
        line = _strip_comment(raw).rstrip()
        if line.strip():
            lines.append(line)
    return ("\n".join(lines) + "\n").encode("ascii") if lines else b""


_SHA1_RE = re.compile(r"\b([0-9a-fA-F]{40})\b")
#: G-codes whose ``ok`` legitimately arrives late (macros, waits, heat-up)
_LONG_CODES = frozenset({"G28", "G29", "G30", "G32", "M98", "M400", "M116",
                         "M109", "M190", "M191", "M303", "G4", "M29"})
_MOTION_CODES = frozenset({"G0", "G1", "G2", "G3"})


def _code_of(line: str) -> str:
    word = line.strip().split(None, 1)[0] if line.strip() else ""
    m = re.match(r"([GMT])0*(\d+)", word.upper())
    return f"{m.group(1)}{m.group(2)}" if m else word.upper()


class DuetSerialClient:
    """Thread-safe RRF client over one USB serial port."""

    READ_RETRIES = 3
    RETRY_DELAY_S = 0.5
    READ_POLL_S = 0.1
    WRITE_TIMEOUT_S = 10.0
    SETTLE_S = 0.2
    #: motion acks wait for a free planner slot (the 60-move look-ahead)
    MOTION_TIMEOUT_S = 180.0
    #: macros (homing), M400 and heat waits
    LONG_TIMEOUT_S = 600.0
    #: M28: if the first file line gets no ``ok`` in this time, the board does
    #: not ack file lines; continue pipelined and rely on the final sync+SHA-1
    FIRST_FILE_ACK_S = 3.0
    UPLOAD_CHUNK_LINES = 32
    MAX_REPLY_LINES = 200

    #: execution strategies upload ``normalize_upload(program)``
    strips_upload_comments = True
    transport = "usb"

    def __init__(self, port: str = AUTO_PORT, baud: int = 115200,
                 timeout_s: float = 20.0,
                 link_factory: Optional[LinkFactory] = None) -> None:
        self.port_setting = port or AUTO_PORT
        self.port: Optional[str] = None      # resolved device, once connected
        self._baud = baud
        self._timeout = timeout_s
        self._factory: LinkFactory = link_factory or _pyserial_factory
        self._link: Optional[SerialLink] = None
        self._lock = threading.RLock()        # one command at a time
        self._write_lock = threading.Lock()   # whole lines; e-stop bypasses _lock
        self._rx = b""
        self._desynced = True
        self._replies: list[str] = []
        #: interface parity with DuetClient: USB needs no buffer accounting,
        #: the per-line ``ok`` is the flow control
        self.avail = BUFF_TOTAL
        self.firmware = ""

    @property
    def base(self) -> str:
        return f"usb:{self.port or self.port_setting}"

    # -------------------------------------------------------------- lifecycle
    def connect(self) -> None:
        """(Re)open the port and synchronise. Safe to call repeatedly: after
        M999 the board re-enumerates and the old handle is dead."""
        with self._lock:
            self._close_link()
            port = (find_duet_port() if self.port_setting.lower() == AUTO_PORT
                    else self.port_setting)
            self._link = self._factory(port, self._baud)
            self.port = port
            time.sleep(self.SETTLE_S)        # let the CDC endpoint settle
            self._rx = b""
            self._desynced = True
            try:
                self._resync()
            except BaseException:
                self._close_link()  # Windows ports are exclusive: release it
                raise
            logger.info("connected over USB on %s: %s", port, self.firmware)

    def close(self) -> None:
        with self._lock:
            self._close_link()

    def _close_link(self) -> None:
        with self._write_lock:
            link, self._link = self._link, None
        self._discard(link)
        self._rx = b""
        self._desynced = True

    @staticmethod
    def _discard(link: Optional[SerialLink]) -> None:
        """Close a dead handle. Merely forgetting it would keep the COM port
        open, and Windows would refuse the reconnect ("access denied")."""
        if link is not None:
            try:
                link.close()
            except Exception:
                pass

    # -------------------------------------------------------------- plumbing
    def _write(self, text: str) -> None:
        with self._write_lock:
            link = self._link
            if link is None:
                raise DuetConnectionError(f"{self.base}: port is not open")
            try:
                link.write(text.encode("ascii", "replace"))
            except OSError as exc:  # serial.SerialException is an OSError
                self._link = None
                self._discard(link)
                raise DuetConnectionError(f"{self.base}: write failed: {exc}") \
                    from exc

    def _readline(self, deadline: float) -> Optional[str]:
        """One complete reply line (stripped), or None at the deadline.
        pyserial's readline can return a partial line on timeout: keep it."""
        while True:
            if b"\n" in self._rx:
                raw, self._rx = self._rx.split(b"\n", 1)
                return raw.decode("utf-8", "replace").strip()
            if time.monotonic() > deadline:
                return None
            link = self._link
            if link is None:
                raise DuetConnectionError(f"{self.base}: port is not open")
            try:
                chunk = link.readline()
            except OSError as exc:
                with self._write_lock:
                    if self._link is link:
                        self._link = None
                self._discard(link)
                raise DuetConnectionError(f"{self.base}: read failed: {exc}") \
                    from exc
            if chunk:
                self._rx += chunk.replace(b"\r", b"")

    def _await_ok(self, timeout_s: float, context: str) -> list[str]:
        """Collect reply lines until ``ok``. Timeout -> desync + raise."""
        lines: list[str] = []
        deadline = time.monotonic() + timeout_s
        while True:
            line = self._readline(deadline)
            if line is None:
                self._desynced = True
                raise DuetConnectionError(
                    f"{self.base}: no 'ok' for {context!r} within "
                    f"{timeout_s:.0f} s")
            if not line:
                continue
            if line.lower().startswith("ok"):
                tail = line[2:].strip()
                if tail:
                    lines.append(tail)
                return lines
            lines.append(line)

    def _resync(self) -> None:
        """Discard stale lines: send M115, read until its FIRMWARE_NAME line
        and the ``ok`` after it. Also proves the far end is RRF."""
        self._write("M115\n")
        deadline = time.monotonic() + self._timeout
        while True:
            line = self._readline(deadline)
            if line is None:
                raise DuetConnectionError(
                    f"{self.base}: no M115 reply within {self._timeout:.0f} s "
                    f"(is this a Duet? is the board still booting?)")
            if "FIRMWARE_NAME" in line:
                break
        if "RepRapFirmware" not in line:
            raise DuetConnectionError(
                f"{self.base}: not RepRapFirmware ({line[:80]!r})")
        self.firmware = line
        self._await_ok(self._timeout, "M115")
        self._desynced = False

    def _timeout_for(self, line: str) -> float:
        code = _code_of(line)
        if code in _LONG_CODES:
            return max(self._timeout, self.LONG_TIMEOUT_S)
        if code in _MOTION_CODES:
            return max(self._timeout, self.MOTION_TIMEOUT_S)
        return self._timeout

    def _command(self, line: str, timeout_s: Optional[float] = None) -> list[str]:
        """Send ONE line and return its reply lines. Caller holds _lock."""
        if self._link is None:
            raise DuetConnectionError(f"{self.base}: not connected")
        if self._desynced:
            self._resync()
        self._write(line + "\n")
        return self._await_ok(timeout_s or self._timeout_for(line), line)

    def _keep_replies(self, lines: list[str]) -> None:
        self._replies.extend(lines)
        del self._replies[:-self.MAX_REPLY_LINES]

    # ------------------------------------------------------------------- api
    def model(self, key: str, retries: Optional[int] = None) -> Any:
        """``M409 K"key"`` -> the object-model value (same as rr_model's
        ``result``). Idempotent: retried, reopening the port if it vanished."""
        attempts = (self.READ_RETRIES if retries is None else retries) + 1
        last: Optional[Exception] = None
        for attempt in range(attempts):
            try:
                with self._lock:
                    if self._link is None and attempt > 0:
                        self.connect()
                    lines = self._command(f'M409 K"{key}"')
                for i, line in enumerate(lines):
                    if line.startswith("{"):
                        self._keep_replies(lines[:i] + lines[i + 1:])
                        return json.loads(line).get("result")
                raise DuetConnectionError(
                    f"{self.base}: M409 {key!r} returned no JSON ({lines[:3]!r})")
            except json.JSONDecodeError as exc:
                last = DuetConnectionError(f"{self.base}: M409 {key!r}: {exc}")
            except DuetConnectionError as exc:
                last = exc
            if attempt + 1 < attempts:
                time.sleep(self.RETRY_DELAY_S * (attempt + 1))
        raise last  # type: ignore[misc]

    def send(self, gcode: str) -> None:
        """Send G-code, one line per ``ok``. Never retried (a re-sent move is
        a duplicated move). Replies accumulate for ``reply()``."""
        lines = [ln.strip() for ln in gcode.split("\n") if ln.strip()]
        for line in lines:
            if len(line.encode("ascii", "replace")) + 1 > BUFF_TOTAL - 1:
                raise DuetLineTooLong(
                    f"line of {len(line)} bytes exceeds the board's "
                    f"{BUFF_TOTAL}-byte line buffer")
        with self._lock:
            for line in lines:
                self._keep_replies(self._command(line))

    def send_emergency(self, gcode: str) -> None:
        """M112/M999: written immediately, bypassing the command lock (a
        running G28 holds it for up to minutes). M999 reboots the board and
        kills the USB device, so the handle is closed after it."""
        self._write(gcode.strip() + "\n")
        self._desynced = True               # its 'ok' arrives out of band
        if _code_of(gcode) == "M999":
            with self._write_lock:
                link, self._link = self._link, None
            self._discard(link)

    def reply(self) -> str:
        out = "\n".join(self._replies)
        self._replies = []
        return out

    def wait_for_space(self, need: int, deadline_s: float = 30.0) -> bool:
        """Always true: over USB each line waits for its own ``ok``."""
        return True

    def upload(self, board_path: str, data: bytes) -> None:
        """Write a file to the SD card with M28/M29, verified by M38 SHA-1.

        ``data`` is normalised first (see ``normalize_upload``); the board
        must end up holding exactly those bytes or this raises."""
        body = normalize_upload(data)
        lines = body.decode("ascii").splitlines()
        expected = hashlib.sha1(body).hexdigest()
        with self._lock:
            reply = self._command(f'M28 "{board_path}"')
            if not any("writing to file" in r.lower() for r in reply) or \
                    any(r.lower().startswith("error") for r in reply):
                raise DuetError(
                    f"M28 {board_path}: board did not open the file for "
                    f"writing ({' | '.join(reply) or 'no reply'}); nothing "
                    f"was sent")
            failure: Optional[BaseException] = None
            try:
                self._write_file_lines(board_path, lines)
            except BaseException as exc:
                failure = exc
            # always leave file-writing mode, then resync: per-line 'ok's
            # may still be in flight
            try:
                self._write("M29\n")
                self._desynced = True
                self._resync()
            except DuetError:
                if failure is None:
                    raise
                logger.exception("could not close %s after a failed upload",
                                 board_path)
            if failure is not None:
                raise failure
            sha_reply = self._command(f'M38 "{board_path}"')
        match = next((m for r in sha_reply for m in [_SHA1_RE.search(r)] if m),
                     None)
        if match is None:
            raise DuetError(f"upload {board_path}: cannot verify, M38 replied "
                            f"{' | '.join(sha_reply) or 'nothing'}")
        if match.group(1).lower() != expected:
            raise DuetError(
                f"upload {board_path}: SHA-1 mismatch (board {match.group(1)}, "
                f"sent {expected}) - the file on the card is NOT what was "
                f"sent; do not run it")
        logger.info("uploaded %s over USB (%d lines, %d bytes, SHA-1 ok)",
                    board_path, len(lines), len(body))

    def _write_file_lines(self, board_path: str, lines: list[str]) -> None:
        """Lockstep (wait for each ``ok``) while the board acks file lines;
        pipelined if it does not. USB CDC flow control prevents overruns."""
        acked = True
        i = 0
        while i < len(lines):
            if acked:
                self._write(lines[i] + "\n")
                limit = self.FIRST_FILE_ACK_S if i == 0 else self._timeout
                try:
                    reply = self._await_ok(limit, f"{board_path} line {i + 1}")
                except DuetConnectionError:
                    if i != 0:
                        raise
                    logger.info("board does not ack M28 file lines; "
                                "pipelining (verified by SHA-1 at the end)")
                    acked = False
                    i += 1
                    continue
                errors = [r for r in reply if r.lower().startswith("error")]
                if errors:
                    raise DuetError(f"upload {board_path} line {i + 1}: "
                                    f"{errors[0]}")
                i += 1
            else:
                chunk = lines[i:i + self.UPLOAD_CHUNK_LINES]
                self._write("".join(f"{ln}\n" for ln in chunk))
                i += len(chunk)

    def download(self, board_path: str) -> bytes:
        raise DuetError(
            f"cannot download {board_path} over USB (RRF has no serial file "
            f"read); use the Ethernet transport")

    def filelist(self, dirname: str) -> list[dict]:
        """``M20 S2 P"dir"`` -> rr_filelist-shaped entries (name/type)."""
        with self._lock:
            lines = self._command(f'M20 S2 P"{dirname}"')
        for line in lines:
            if line.startswith("{"):
                names = json.loads(line).get("files", [])
                return [{"type": "d", "name": n[1:]} if n.startswith("*")
                        else {"type": "f", "name": n} for n in names]
        raise DuetError(f"M20 {dirname}: no JSON listing ({lines[:3]!r})")

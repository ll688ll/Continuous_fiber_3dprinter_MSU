"""HTTP client for the RepRapFirmware standalone web API (rr_* endpoints).

Productionized from the proven prototype in ``firmware/duet3_6xd/
stream_gcode.py`` (commissioning sessions 2026-08-25). What it adds:

* an injectable ``opener`` seam so tests run against an in-process fake board;
* HTTP status + JSON checking on every request;
* bounded retry with backoff on *idempotent* reads only — a ``rr_gcode``
  payload is NEVER retried (re-sending motion is worse than stalling);
* one lock serializing all requests: the status poller, the job worker, and
  UI tasks share this client and RRF serves one request at a time anyway;
* ``send()`` refuses payloads larger than the free buffer — ``rr_gcode``
  SILENTLY DROPS overflow (no error, missing motion), the single nastiest
  trap of the whole API (measured 2026-08-25);
* ``upload()`` (POST ``rr_upload`` with CRC32 verification).

All methods raise ``DuetError`` subclasses; transport problems surface as
``DuetConnectionError`` after retries are exhausted.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from typing import Any, Callable, Optional

from ....errors import PrinterError

#: RRF's G-code input buffer over HTTP, bytes (measured; board-reported `buff`
#: never exceeds this). One line + newline must fit in BUFF_TOTAL - 1.
BUFF_TOTAL = 250
#: keep this much headroom before feeding more (prototype-proven watermark)
BUFF_FLOOR = 60

#: (url, data, timeout_s) -> bytes. ``data`` is None for GET.
Opener = Callable[[str, Optional[bytes], float], bytes]


class DuetError(PrinterError):
    """Base for Duet client/adapter errors."""


class DuetConnectionError(DuetError):
    """Transport failure (after retries) or connect refusal."""


class DuetLineTooLong(DuetError):
    """A single G-code line cannot ever fit the board's input buffer."""


def _default_opener(url: str, data: Optional[bytes], timeout: float) -> bytes:
    with urllib.request.urlopen(url, data=data, timeout=timeout) as resp:
        status = getattr(resp, "status", 200)
        if status != 200:
            raise DuetConnectionError(f"HTTP {status} from {url}")
        return resp.read()


class DuetClient:
    """Thread-safe client for one RRF board in standalone mode."""

    #: retries for idempotent reads (model/reply/connect); writes get none
    READ_RETRIES = 3
    RETRY_DELAY_S = 0.4

    def __init__(self, host: str, password: str = "",
                 timeout_s: float = 20.0,
                 opener: Optional[Opener] = None) -> None:
        self.base = f"http://{host}"
        self._password = password
        self._timeout = timeout_s
        self._opener: Opener = opener or _default_opener
        self._lock = threading.RLock()
        #: last known free buffer space; fail-closed (0) on parse trouble
        self.avail = BUFF_TOTAL

    # -------------------------------------------------------------- plumbing
    def _request(self, path: str, data: Optional[bytes] = None,
                 retries: int = 0, timeout: Optional[float] = None) -> bytes:
        url = self.base + path
        last_exc: Optional[Exception] = None
        with self._lock:
            for attempt in range(retries + 1):
                try:
                    return self._opener(url, data, timeout or self._timeout)
                except DuetError:
                    raise
                except (urllib.error.URLError, OSError, TimeoutError) as exc:
                    last_exc = exc
                    if attempt < retries:
                        time.sleep(self.RETRY_DELAY_S * (attempt + 1))
        raise DuetConnectionError(
            f"{url}: {last_exc}") from last_exc

    def _get_json(self, path: str, retries: int = 0) -> Any:
        raw = self._request(path, retries=retries)
        try:
            return json.loads(raw.decode("utf-8", "replace"))
        except json.JSONDecodeError as exc:
            raise DuetConnectionError(
                f"{path}: response is not JSON ({raw[:80]!r})") from exc

    # ------------------------------------------------------------------- api
    def connect(self) -> None:
        reply = self._get_json(
            "/rr_connect?password=" + urllib.parse.quote(self._password),
            retries=self.READ_RETRIES)
        err = reply.get("err", 0)
        if err != 0:
            raise DuetConnectionError(f"rr_connect refused (err={err}; "
                                      f"wrong password?)")
        self.avail = BUFF_TOTAL

    def model(self, key: str, retries: Optional[int] = None) -> Any:
        """Read one object-model key (e.g. 'move.axes', 'state.status',
        'job.duration'). Idempotent -> retried by default."""
        r = self.READ_RETRIES if retries is None else retries
        reply = self._get_json(
            "/rr_model?key=" + urllib.parse.quote(key), retries=r)
        return reply.get("result")

    def send(self, gcode: str) -> None:
        """Queue G-code into the board's input buffer.

        Refuses payloads that exceed the last known free space — the board
        would accept the request and SILENTLY DROP the overflow. Callers
        batch with ``wait_for_space(len(payload) + 1)`` first.
        Never retried: a re-sent motion command is a duplicated move.
        """
        payload = gcode.encode("ascii", "replace")
        if len(payload) + 1 > BUFF_TOTAL - 1:
            raise DuetLineTooLong(
                f"{len(payload) + 1} bytes exceeds the {BUFF_TOTAL}-byte board "
                f"buffer; split the payload")
        if gcode and len(payload) + 1 > self.avail:
            raise DuetError(
                f"payload needs {len(payload) + 1} bytes but only {self.avail} "
                f"are free; call wait_for_space() first")
        reply = self._get_json(
            "/rr_gcode?gcode=" + urllib.parse.quote(gcode))
        try:
            self.avail = int(reply.get("buff"))
        except (TypeError, ValueError):
            self.avail = 0  # fail closed; wait_for_space() will refresh

    def send_emergency(self, gcode: str) -> None:
        """M112/M999 path: bypasses the buffer bookkeeping (RRF prioritizes
        emergency codes) and uses a short timeout."""
        self._request("/rr_gcode?gcode=" + urllib.parse.quote(gcode),
                      timeout=5.0)
        self.avail = 0

    def reply(self) -> str:
        raw = self._request("/rr_reply", retries=self.READ_RETRIES)
        return raw.decode("utf-8", "replace").strip()

    def wait_for_space(self, need: int, deadline_s: float = 30.0) -> bool:
        """Block until at least ``need`` bytes (and the floor) are free.
        An empty rr_gcode request queues nothing but refreshes ``buff``."""
        need = max(need, BUFF_FLOOR)
        end = time.monotonic() + deadline_s
        while self.avail < need:
            if time.monotonic() > end:
                return False
            time.sleep(0.02)
            reply = self._get_json("/rr_gcode?gcode=")
            try:
                self.avail = int(reply.get("buff"))
            except (TypeError, ValueError):
                self.avail = 0
        return True

    def upload(self, board_path: str, data: bytes) -> None:
        """POST a file to the board (e.g. '/sys/msu_machine.g',
        '/gcodes/msu_jobs/job.g') with CRC32 verification."""
        crc = zlib.crc32(data) & 0xFFFFFFFF
        path = ("/rr_upload?name=" + urllib.parse.quote(board_path)
                + f"&crc32={crc:08x}")
        reply_raw = self._request(path, data=data)
        try:
            err = json.loads(reply_raw.decode("utf-8", "replace")).get("err", 1)
        except json.JSONDecodeError:
            err = 1
        if err != 0:
            raise DuetError(
                f"rr_upload {board_path} failed (err={err}) — CRC mismatch or "
                f"SD trouble; nothing was applied")

    def filelist(self, dirname: str) -> list[dict]:
        reply = self._get_json(
            "/rr_filelist?dir=" + urllib.parse.quote(dirname),
            retries=self.READ_RETRIES)
        return list(reply.get("files", []))

"""ScriptedDuetBoard: an in-process fake RRF board at the DuetClient opener seam.

Faithful where it matters (behaviors that cost real debugging time on the lab
machine, 2026-08-25):

* the 250-byte G-code input buffer, with **silent overflow drop** — bytes past
  the free space vanish with no error, exactly like the real ``rr_gcode``;
* replies to refused commands (e.g. moving an unhomed axis) appear only in
  ``rr_reply``, never as HTTP errors;
* ``M112`` halts until ``M999``, and ``M999`` reboots: homed flags clear,
  positions zero, and the board drops one connection cycle;
* ``rr_upload`` verifies the crc32 query parameter against the body.

``FakeUsbDuet`` puts the same board behind a USB serial link (RRF's serial
dialect: replies then ``ok``), so both transports test against one model.

Deterministic: ``state.status`` reads "processing" for ``busy_reads`` polls
after each motion command, then "idle" — no wall-clock involved.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import zlib
from typing import Optional

BUFF_TOTAL = 250


class ScriptedDuetBoard:
    """Callable opener: ``opener(url, data, timeout) -> bytes``."""

    def __init__(self, axes: Optional[dict[str, dict]] = None,
                 busy_reads: int = 2) -> None:
        # letter -> {machinePosition, homed, min, max, stepsPerMm}
        self.axes = axes if axes is not None else {
            "X": {"stepsPerMm": 142.857, "min": 0.0, "max": 987.448},
            "Y": {"stepsPerMm": 142.857, "min": 0.0, "max": 290.269},
            "Z": {"stepsPerMm": 500.0, "min": 0.0, "max": 282.306},
        }
        for ax in self.axes.values():
            ax.setdefault("machinePosition", 0.0)
            ax.setdefault("homed", False)
        self.busy_reads = busy_reads
        self._busy_left = 0
        self.halted = False
        self.relative = False
        self.avail = BUFF_TOTAL
        #: bytes drained from the buffer per subsequent request; None = all
        self.drain_per_request: Optional[int] = None

        self.command_log: list[str] = []      # every executed G-code line
        self.pending_reply: list[str] = []
        self.files: dict[str, bytes] = {}
        self.overflow_dropped = 0             # bytes silently discarded
        self.connect_calls = 0

        # fault injection
        self.fail_connect = False
        self.transient_failures = 0           # next N requests raise URLError
        self.refuse_moves_unhomed = True      # RRF M564 default behavior

        # SD-job model (M32/M25/M24/M0); file position advances per
        # state.status read for determinism
        self.job: dict = {}                   # object-model job subtree
        self.job_reads_to_complete = 4
        self._job_reads_left = 0
        self._job_total_bytes = 0
        self._job_paused = False
        self._job_data = b""
        self.start_wrong_file: str | None = None  # inject the ghost-job bug

    # ------------------------------------------------------------ the opener
    def __call__(self, url: str, data: Optional[bytes], timeout: float) -> bytes:
        if self.transient_failures > 0:
            self.transient_failures -= 1
            raise urllib.error.URLError("injected transient failure")
        parsed = urllib.parse.urlparse(url)
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        path = parsed.path
        if path == "/rr_connect":
            return self._connect()
        if path == "/rr_model":
            return self._model(query.get("key", [""])[0])
        if path == "/rr_gcode":
            return self._gcode(query.get("gcode", [""])[0])
        if path == "/rr_reply":
            out = "\n".join(self.pending_reply)
            self.pending_reply = []
            return out.encode()
        if path == "/rr_upload":
            return self._upload(query.get("name", [""])[0],
                                query.get("crc32", [""])[0], data or b"")
        if path == "/rr_download":
            name = query.get("name", [""])[0]
            if name not in self.files:
                raise urllib.error.URLError(f"no such file {name}")
            return self.files[name]
        if path == "/rr_filelist":
            names = [{"type": "f", "name": n.rsplit("/", 1)[-1]}
                     for n in self.files]
            return json.dumps({"dir": query.get("dir", [""])[0],
                               "files": names}).encode()
        raise urllib.error.URLError(f"unknown path {path}")

    # -------------------------------------------------------------- handlers
    def _connect(self) -> bytes:
        self.connect_calls += 1
        if self.fail_connect:
            return json.dumps({"err": 1}).encode()
        return json.dumps({"err": 0}).encode()

    def _status(self) -> str:
        if self.halted:
            return "halted"
        if self.job.get("file", {}).get("fileName"):
            if self._job_paused:
                return "paused"
            # advance the running job one deterministic step per status read
            self._job_reads_left -= 1
            step = max(1, self._job_total_bytes // self.job_reads_to_complete)
            self.job["filePosition"] = min(
                self._job_total_bytes, self.job.get("filePosition", 0) + step)
            self.job["duration"] = self.job.get("duration", 0.0) + 1.5
            if self._job_reads_left <= 0:
                self._finish_job()
            return "processing"
        if self._busy_left > 0:
            self._busy_left -= 1
            return "processing"
        return "idle"

    def _finish_job(self) -> None:
        # a real board moved while printing: replay the file's motion so the
        # axes end up where the program says (end-of-job verification)
        for raw in self._job_data.decode("ascii", "replace").splitlines():
            line = raw.split(";", 1)[0].strip()
            if not line:
                continue
            code = line.split()[0].upper()
            if code in ("M2", "M30", "M32"):
                continue
            self._execute(line)
        self.job = {"file": {"fileName": None},
                    "lastDuration": self.job.get("duration", 0.0),
                    "filePosition": None}
        self._job_paused = False
        self._job_data = b""

    def _model(self, key: str) -> bytes:
        if key == "state.status":
            result: object = self._status()
        elif key == "move.axes":
            result = [{"letter": letter, **props}
                      for letter, props in self.axes.items()]
        elif key.startswith("job"):
            result = self.job
            for part in key.split(".")[1:]:
                result = result.get(part) if isinstance(result, dict) else None
        else:
            result = None
        return json.dumps({"key": key, "result": result}).encode()

    def _gcode(self, gcode: str) -> bytes:
        # drain first (the board consumed some of what was queued before)
        if self.drain_per_request is None:
            self.avail = BUFF_TOTAL
        else:
            self.avail = min(BUFF_TOTAL, self.avail + self.drain_per_request)
        payload = gcode.encode("ascii", "replace")
        if payload:
            cost = len(payload) + 1
            if cost > self.avail:
                # THE trap: the real board silently drops the overflow
                accepted = payload[:max(0, self.avail - 1)]
                self.overflow_dropped += len(payload) - len(accepted)
                payload = accepted
                self.avail = 0
            else:
                self.avail -= cost
            text = payload.decode("ascii", "replace")
            complete = text.split("\n")
            # a truncated trailing line is garbage on the real board too;
            # execute only what parses as complete lines
            for line in complete:
                if line.strip():
                    self._execute(line.strip())
        return json.dumps({"buff": self.avail}).encode()

    def _execute(self, line: str) -> None:
        self.command_log.append(line)
        code = line.split()[0].upper()
        if code == "M112":
            self.halted = True
            self.job = {}
            return
        if code == "M32":
            name = line.split(None, 1)[1].strip().strip('"')
            # RRF accepts both "0:/gcodes/x" and "/gcodes/x"; normalize
            lookup = name[2:] if name.startswith("0:") else name
            if self.start_wrong_file is not None:
                name = self.start_wrong_file  # ghost-job injection
            elif lookup not in self.files:
                self.pending_reply.append(f"Error: GCode file not found: {name}")
                return
            data = self.files.get(lookup, b"")
            self._job_data = data
            self._job_total_bytes = len(data)
            self._job_reads_left = self.job_reads_to_complete
            self._job_paused = False
            self.job = {"file": {"fileName": name}, "filePosition": 0,
                        "duration": 0.0}
            self.pending_reply.append(f"File {name} selected for printing")
            return
        if code == "M25":
            self._job_paused = True
            return
        if code == "M24":
            self._job_paused = False
            return
        if code == "M0":
            if self.job.get("file", {}).get("fileName"):
                self._finish_job()
            return
        if code == "M999":
            self.halted = False
            self.relative = False
            for ax in self.axes.values():
                ax["homed"] = False
                ax["machinePosition"] = 0.0
            return
        if self.halted:
            return  # a halted board executes nothing else
        if code == "G90":
            self.relative = False
        elif code == "G91":
            self.relative = True
        elif code == "G28":
            letters = [w[0] for w in line.split()[1:]] or list(self.axes)
            for letter in letters:
                if letter in self.axes:
                    self.axes[letter]["homed"] = True
                    self.axes[letter]["machinePosition"] = \
                        self.axes[letter]["min"]
            self._busy_left = self.busy_reads
        elif code in ("G0", "G1"):
            words = dict(re.findall(r"([A-Z])(-?\d+\.?\d*)", line[2:].strip()))
            words.pop("F", None)
            unhomed = [w for w in words if w in self.axes
                       and not self.axes[w]["homed"]]
            if unhomed and self.refuse_moves_unhomed:
                self.pending_reply.append(
                    f"Error: insufficient axes homed for G1 ({unhomed})")
                return
            for letter, value in words.items():
                if letter not in self.axes:
                    continue
                v = float(value)
                ax = self.axes[letter]
                target = ax["machinePosition"] + v if self.relative else v
                # RRF M564 S1 (default) SILENTLY CLAMPS to M208 rather than
                # rejecting — confirmed on hardware 2026-08-30. The fake is
                # faithful so a missing host-side check fails position
                # assertions instead of passing quietly.
                ax["machinePosition"] = min(max(target, ax["min"]), ax["max"])
            self._busy_left = self.busy_reads

    def _upload(self, name: str, crc_hex: str, data: bytes) -> bytes:
        if crc_hex:
            expect = int(crc_hex, 16)
            if (zlib.crc32(data) & 0xFFFFFFFF) != expect:
                return json.dumps({"err": 1}).encode()
        self.files[name] = data
        return json.dumps({"err": 0}).encode()


# =================================================================== USB side
class FakeUsbDuet:
    """The same ScriptedDuetBoard behind a USB serial link.

    Use as ``DuetSerialClient(link_factory=fake)``: ``fake(port, baud)``
    returns a ``FakeSerialLink``. Speaks RRF's serial dialect: reply lines,
    then ``ok`` per command; ``M115``/``M409``/``M28``-``M29``/``M38``/``M20``.

    Fault injection: ``drop_ok`` (commands whose ``ok`` never comes),
    ``ack_file_lines`` (does M28 file mode ack each line), ``refuse_m28``,
    ``corrupt_upload``, ``firmware_name``, ``present`` (device enumerated).
    M999 kills the open link like the real re-enumerating USB device.
    """

    FIRMWARE = ("FIRMWARE_NAME: RepRapFirmware for Duet 3 MB6XD "
                "FIRMWARE_VERSION: 3.6.1 ELECTRONICS: Duet 3 MB6XD v1.02")

    def __init__(self, board: Optional[ScriptedDuetBoard] = None) -> None:
        self.board = board or ScriptedDuetBoard()
        self.out: list[bytes] = []
        self.received: list[str] = []          # every line that arrived
        self.opened: list[str] = []            # ports opened, in order
        self.link: Optional["FakeSerialLink"] = None
        self.present = True
        self.firmware_name = self.FIRMWARE
        self.ack_file_lines = True
        self.refuse_m28 = False
        self.corrupt_upload = False
        self.drop_ok: set[str] = set()         # exact lines; ok dropped once
        self.writing: Optional[str] = None
        self._file_lines: list[str] = []

    def __call__(self, port: str, baud: int) -> "FakeSerialLink":
        if not self.present:
            raise OSError(f"could not open port {port!r}: FileNotFoundError")
        self.opened.append(port)
        self.out = []
        self.link = FakeSerialLink(self)
        return self.link

    # ---------------------------------------------------------------- output
    def emit(self, *lines: str) -> None:
        self.out.extend(f"{ln}\n".encode() for ln in lines)

    def ok(self, line: str) -> None:
        if line in self.drop_ok:
            self.drop_ok.discard(line)
            return
        self.emit("ok")

    @staticmethod
    def _quoted(line: str) -> str:
        m = re.search(r'"([^"]*)"', line)
        return m.group(1) if m else ""

    @staticmethod
    def _card_key(path: str) -> str:
        return path[2:] if path.startswith("0:") else path

    # ------------------------------------------------------------- dispatch
    def handle(self, line: str) -> None:
        line = line.strip()
        if not line:
            return
        self.received.append(line)
        code = line.split()[0].upper()
        if self.writing is not None and code != "M29":
            self._file_lines.append(line)
            if self.ack_file_lines:
                self.ok(line)
            return
        if code == "M115":
            self.emit(self.firmware_name)
        elif code == "M409":
            key = self._quoted(line)
            self.emit(self.board._model(key).decode())
        elif code == "M28":
            path = self._quoted(line)
            if self.refuse_m28:
                self.emit(f"Error: Can't open file {path} for writing.")
            else:
                self.writing = path
                self._file_lines = []
                self.emit(f"Writing to file: {path}")
        elif code == "M29":
            data = ("\n".join(self._file_lines) + "\n").encode() \
                if self._file_lines else b""
            if self.corrupt_upload:
                data = data.replace(b"X", b"Y", 1)
            self.board.files[self._card_key(self.writing or "")] = data
            self.writing = None
            self.emit("Done saving file.")
        elif code == "M38":
            key = self._card_key(self._quoted(line))
            if key not in self.board.files:
                self.emit(f"Error: M38: File {key} not found")
            else:
                digest = hashlib.sha1(self.board.files[key]).hexdigest()
                self.emit(f"SHA1 hash of file {key}: {digest}")
        elif code == "M20":
            names = [n.rsplit("/", 1)[-1] for n in self.board.files]
            self.emit(json.dumps({"dir": self._quoted(line), "first": 0,
                                  "files": names + ["*msu_jobs"], "next": 0}))
        elif code == "M999":
            self.board._execute("M999")
            if self.link is not None:
                self.link.dead = True     # the device re-enumerates
            return
        else:
            self.board._execute(line)
            if self.board.pending_reply:
                self.emit(*self.board.pending_reply)
                self.board.pending_reply = []
        self.ok(line)


class FakeSerialLink:
    """``serial.Serial`` stand-in: readline() returns b"" when idle."""

    def __init__(self, device: FakeUsbDuet) -> None:
        self.device = device
        self.closed = False
        self.dead = False
        self._partial = b""

    def _check(self) -> None:
        if self.closed or self.dead:
            raise OSError("ClearCommError failed (PermissionError: the "
                          "device does not recognize the command)")

    def write(self, data: bytes) -> int:
        self._check()
        buf = self._partial + data
        *complete, self._partial = buf.split(b"\n")
        for raw in complete:
            self.device.handle(raw.decode("ascii", "replace"))
        return len(data)

    def readline(self) -> bytes:
        self._check()
        if self.device.out and self.device.link is self:
            return self.device.out.pop(0)
        time.sleep(0.001)
        return b""

    def close(self) -> None:
        self.closed = True

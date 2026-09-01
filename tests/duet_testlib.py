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

Deterministic: ``state.status`` reads "processing" for ``busy_reads`` polls
after each motion command, then "idle" — no wall-clock involved.
"""

from __future__ import annotations

import json
import re
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
        self.job: dict = {}                   # object-model job subtree

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
        if self._busy_left > 0:
            self._busy_left -= 1
            return "processing"
        return "idle"

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
                ax["machinePosition"] = (ax["machinePosition"] + v
                                         if self.relative else v)
            self._busy_left = self.busy_reads

    def _upload(self, name: str, crc_hex: str, data: bytes) -> bytes:
        if crc_hex:
            expect = int(crc_hex, 16)
            if (zlib.crc32(data) & 0xFFFFFFFF) != expect:
                return json.dumps({"err": 1}).encode()
        self.files[name] = data
        return json.dumps({"err": 0}).encode()

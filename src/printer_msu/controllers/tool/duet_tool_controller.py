"""Duet tool controller: heaters / thermocouple / fan / extruder over RRF.

Shares the motion adapter's ``DuetClient`` (one lock, one board). ``channel``
in the BaseToolController contract maps to the profile TOOL INDEX — the board
tool defined by the generated ``M563 P<tool>``.

Safety posture (V2-M4 hardware gates):
* the generated config always carries ``M143`` (hard temperature ceiling) and
  the tool is defined COLD (``M568 R0 S0``);
* RRF's own heater-fault detection and cold-extrusion guard (M302 default ON)
  stay active — a refused extrude surfaces as an error, which is correct;
* ``emergency_shutdown`` turns every configured heater and fan off; cutting
  motion is the motion adapter's M112, not this class's job.
"""

from __future__ import annotations

import logging
from typing import Mapping

from ...config.profile import MachineProfile, ToolConfig
from ..motion.duet.client import DuetClient, DuetError
from .base_tool_controller import (
    BaseToolController,
    TemperatureStatus,
    ToolStatus,
)

logger = logging.getLogger("printer_msu.duet_tool")


def _fmt(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


class DuetToolController(BaseToolController):
    def __init__(self, client: DuetClient, profile: MachineProfile,
                 controller_id: str) -> None:
        self._client = client
        self._tools: dict[int, ToolConfig] = {
            t.tool_index: t for t in profile.tools
            if any(part is not None and part.controller == controller_id
                   for part in (t.extruder, t.heater, t.fan))}
        self._connected = False

    # ------------------------------------------------------------- lifecycle
    def connect(self) -> None:
        # the shared client is connected by the motion adapter; nothing extra
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    # ----------------------------------------------------------------- tools
    def _tool(self, channel: int) -> ToolConfig:
        try:
            return self._tools[channel]
        except KeyError:
            raise DuetError(f"tool {channel} has no duet tool hardware; "
                            f"configured: {sorted(self._tools)}") from None

    def _send(self, gcode: str) -> None:
        if not self._client.wait_for_space(len(gcode) + 1):
            raise DuetError("board buffer never freed for a tool command")
        self._client.send(gcode)

    def set_target_temperature(self, channel: int, target_c: float) -> None:
        tool = self._tool(channel)
        if tool.heater is None:
            raise DuetError(f"tool {channel} has no heater configured")
        if target_c > tool.heater.max_temp_c:
            raise DuetError(f"{target_c:g} C exceeds tool {channel}'s "
                            f"max_temp_c {tool.heater.max_temp_c:g}")
        self._send(f"M104 T{channel} S{_fmt(target_c)}")

    def heater_off(self, channel: int) -> None:
        self._send(f"M104 T{channel} S0")

    def set_fan_speed(self, channel: int, value: float) -> None:
        tool = self._tool(channel)
        if tool.fan is None:
            raise DuetError(f"tool {channel} has no fan configured")
        value = max(0.0, min(1.0, value))
        if value <= 0.0:
            self._send(f"M107 P{tool.fan.fan_index}")
        else:
            self._send(f"M106 P{tool.fan.fan_index} S{_fmt(value)}")

    def extrude(self, channel: int, distance_mm: float,
                feed_mm_s: float) -> None:
        """Manual extrude/retract. RRF's cold-extrusion guard applies — a
        refusal below min extrude temp is the correct outcome."""
        tool = self._tool(channel)
        if tool.extruder is None:
            raise DuetError(f"tool {channel} has no extruder configured")
        self._send(f"T{channel}\nM83\n"
                   f"G1 E{_fmt(distance_mm)} F{_fmt(feed_mm_s * 60.0)}")
        reply = self._client.reply()
        for line in reply.splitlines():
            if line.strip().lower().startswith("error"):
                raise DuetError(f"extrude refused: {line.strip()}")

    def emergency_shutdown(self) -> None:
        for channel, tool in self._tools.items():
            try:
                if tool.heater is not None:
                    self._send(f"M104 T{channel} S0")
                if tool.fan is not None:
                    self._send(f"M107 P{tool.fan.fan_index}")
            except DuetError:
                logger.exception("emergency shutdown of tool %s failed",
                                 channel)

    # ---------------------------------------------------------------- status
    def get_status(self) -> ToolStatus:
        heaters = self._client.model("heat.heaters") or []
        temperatures: dict[int, TemperatureStatus] = {}
        fans_model = self._client.model("fans") or []
        fans: dict[int, float] = {}
        for channel, tool in self._tools.items():
            if tool.heater is not None and tool.heater.heater_index < len(heaters):
                h = heaters[tool.heater.heater_index] or {}
                temperatures[channel] = TemperatureStatus(
                    current_c=float(h.get("current", 0.0)),
                    target_c=float(h.get("active", 0.0)),
                    heater_on=h.get("state") not in (None, "off"),
                )
            if tool.fan is not None and tool.fan.fan_index < len(fans_model):
                f = fans_model[tool.fan.fan_index] or {}
                fans[channel] = float(f.get("actualValue", 0.0))
        return ToolStatus(temperatures=temperatures, fans=fans)

    def temperatures_dict(self) -> Mapping[int, dict]:
        """UI-friendly per-tool block for full_status()['tools']."""
        status = self.get_status()
        return {ch: {"current_c": t.current_c, "target_c": t.target_c,
                     "heater_on": t.heater_on}
                for ch, t in status.temperatures.items()}

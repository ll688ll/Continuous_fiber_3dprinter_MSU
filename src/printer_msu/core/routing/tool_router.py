from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolRoute:
    """
    Resolved mapping for an active tool.

    In v1:
    - Z is a motion axis on a motion controller
    - Extruder/heater/fan are channels on a Tool Controller
    """

    tool_index: int
    z_axis_id: str
    extruder_channel: int | None = None
    heater_channel: int | None = None
    fan_channel: int | None = None


class ToolRouter:
    """Maps tool index (Tn) to configured axis/channel identifiers."""

    def __init__(self) -> None:
        # Placeholder; will be constructed from Machine Profile.
        self._routes: dict[int, ToolRoute] = {}

    def register(self, route: ToolRoute) -> None:
        self._routes[route.tool_index] = route

    def resolve(self, tool_index: int) -> ToolRoute:
        return self._routes[tool_index]



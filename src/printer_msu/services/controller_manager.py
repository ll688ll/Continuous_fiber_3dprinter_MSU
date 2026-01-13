from __future__ import annotations

"""
ControllerManager owns controller plugin instances and routes calls by mapping.

This is intentionally small right now; it will grow as the Machine Profile schema is implemented.
"""

from dataclasses import dataclass


@dataclass
class ControllerRef:
    controller_id: str


class ControllerManager:
    def __init__(self) -> None:
        self._controllers: dict[str, object] = {}

    def register(self, controller_id: str, controller: object) -> None:
        self._controllers[controller_id] = controller

    def get(self, controller_id: str) -> object:
        return self._controllers[controller_id]



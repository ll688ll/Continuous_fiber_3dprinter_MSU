from __future__ import annotations

"""
StatusService periodically polls controllers and publishes a cached status for the UI.

Threading:
- Implementation should avoid blocking print streaming.
- Default poll interval target is 500 ms (configurable).
"""


class StatusService:
    def __init__(self) -> None:
        self._running = False

    def start(self) -> None:
        self._running = True

    def stop(self) -> None:
        self._running = False



"""Duet 3 / RepRapFirmware backend: HTTP or USB serial client + adapter."""

from __future__ import annotations

from typing import Any, Mapping


def make_duet_client(connection: Mapping[str, Any]):
    """The client for a validated Duet ``connection`` block.

    ``transport: "http"`` (default) -> ``DuetClient`` on ``host``;
    ``transport: "usb"`` -> ``DuetSerialClient`` on ``port`` ("auto" finds
    the single Duet by its USB ID). Both expose the same methods.
    """
    if connection.get("transport", "http") == "usb":
        from .serial_client import DuetSerialClient

        return DuetSerialClient(
            port=connection.get("port", "auto"),
            baud=int(connection.get("baud", 115200)),
            timeout_s=float(connection.get("serial_timeout_s", 20.0)))
    from .client import DuetClient

    return DuetClient(connection["host"],
                      password=connection.get("password", ""),
                      timeout_s=float(connection.get("http_timeout_s", 20.0)))

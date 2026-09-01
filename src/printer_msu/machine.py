"""Machine construction shared by the CLI and the UI.

``build_machine`` instantiates adapters for a v2 profile: one motion adapter
for the controller that owns axes, plus tool controllers where configured.
This is the single place that maps ``ControllerDef.type`` onto backends.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .config.profile import (
    CONTROLLER_DUET,
    CONTROLLER_FMC,
    MachineProfile,
)
from .errors import ProfileError


@dataclass
class Machine:
    """Live adapters built from a profile (not yet connected)."""

    profile: MachineProfile
    motion: object                      # BaseMotionController (wide contract)
    motion_controller_id: str
    tool_controllers: dict[str, object] = field(default_factory=dict)

    def tool_controller_for(self, controller_id: str) -> Optional[object]:
        return self.tool_controllers.get(controller_id)


def build_machine(profile: MachineProfile) -> Machine:
    """Create adapters for the controller that owns the profile's axes."""
    owner = profile.motion_controller

    if owner.type == CONTROLLER_FMC:
        from fmc_motion import create_controller  # deferred: keeps --help fast
        from .controllers.motion.fmc_motion_adapter import FmcMotionAdapter

        assert profile.motion is not None  # guaranteed by the loader
        controller = create_controller(str(profile.motion.config_path),
                                       mode=profile.motion.mode)
        adapter = FmcMotionAdapter(controller, axis_map=profile.motion.axis_map)
        return Machine(profile=profile, motion=adapter,
                       motion_controller_id=owner.id)

    if owner.type == CONTROLLER_DUET:
        from .controllers.motion.duet.adapter import DuetMotionAdapter
        from .controllers.motion.duet.client import DuetClient

        conn = owner.connection
        client = DuetClient(conn["host"],
                            password=conn.get("password", ""),
                            timeout_s=float(conn.get("http_timeout_s", 20.0)))
        adapter = DuetMotionAdapter(client, profile.axes_on(owner.id))
        return Machine(profile=profile, motion=adapter,
                       motion_controller_id=owner.id)

    raise ProfileError(f"unknown controller type {owner.type!r}")  # unreachable


def build_adapter(profile: MachineProfile):
    """Motion adapter for a profile (legacy name; CLI/UI entry point)."""
    return build_machine(profile).motion

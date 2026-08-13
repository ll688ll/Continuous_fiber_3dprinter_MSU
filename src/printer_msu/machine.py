"""Machine construction shared by the CLI and the UI."""

from __future__ import annotations

from .config.profile import MachineProfile
from .controllers.motion.fmc_motion_adapter import FmcMotionAdapter


def build_adapter(profile: MachineProfile) -> FmcMotionAdapter:
    """Create the motion adapter for a profile (not yet connected)."""
    from fmc_motion import create_controller  # deferred: keeps --help fast

    controller = create_controller(str(profile.motion.config_path),
                                   mode=profile.motion.mode)
    return FmcMotionAdapter(controller, axis_map=profile.motion.axis_map)

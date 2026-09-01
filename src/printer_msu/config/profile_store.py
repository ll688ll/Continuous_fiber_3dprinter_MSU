"""Persist machine profiles (canonical schema v2).

Writing goes through a temp file + atomic replace so a crash mid-write can
never leave a half-profile behind (the calibration flow and the setup UI both
write through here). Files are UTF-8 with LF endings on every platform.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .profile import MachineProfile


def save_profile(path: str | Path, profile: MachineProfile) -> Path:
    """Write the profile as canonical v2 JSON; returns the resolved path."""
    target = Path(path)
    data = json.dumps(profile.to_dict(), indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent),
                                    prefix=target.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(data)
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return target

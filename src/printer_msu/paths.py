"""Host-side filesystem locations (job reports and their logs).

Reports live in a ``logs/`` folder under the app, wherever the app is:

- frozen bundle (PyInstaller): next to the executable
- source checkout / editable install: the repository root (the folder that
  holds ``pyproject.toml``), which is the historical ``logs/`` location
- plain wheel install into an environment: that environment's root
  (``sys.prefix``)

The location never depends on the current working directory, so a desktop
shortcut and a terminal session write to the same place.
``PRINTER_MSU_LOG_DIR`` overrides the folder; ``printer-msu print --report``
still names a single report file explicitly. If the app folder cannot be
written (an install under Program Files, say) reports fall back to the
per-user state directory (``LOCALAPPDATA`` on Windows, ``XDG_STATE_HOME``
elsewhere) and a warning is logged.
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger("printer_msu.paths")

ENV_LOG_DIR = "PRINTER_MSU_LOG_DIR"
APP_DIR_NAME = "printer_msu"
LOG_DIR_NAME = "logs"


def app_dir() -> Path:
    """Folder the app runs from (see the module docstring)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    package_dir = Path(__file__).resolve().parent        # <root>/src/printer_msu
    repo_root = package_dir.parent.parent
    if (repo_root / "pyproject.toml").is_file():
        return repo_root
    return Path(sys.prefix)


def default_report_dir() -> Path:
    """Directory that receives ``job-*.json`` reports and their ``.log`` twins."""
    override = os.environ.get(ENV_LOG_DIR)
    if override:
        return Path(override).expanduser()
    preferred = app_dir() / LOG_DIR_NAME
    if _writable(preferred):
        return preferred
    fallback = _user_state_dir() / LOG_DIR_NAME
    logger.warning("report dir %s is not writable; using %s", preferred, fallback)
    return fallback


def _user_state_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Local"
    else:
        base = os.environ.get("XDG_STATE_HOME")
        root = Path(base) if base else Path.home() / ".local" / "state"
    return root / APP_DIR_NAME


def _writable(directory: Path) -> bool:
    """Create the folder and prove a file can be written in it.

    ``os.access`` is not trustworthy for ACL-protected folders on Windows, so
    an actual (auto-deleted) temp file is the probe.
    """
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=directory):
            pass
    except OSError:
        return False
    return True

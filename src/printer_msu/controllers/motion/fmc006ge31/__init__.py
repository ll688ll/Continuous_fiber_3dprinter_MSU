from __future__ import annotations

from pathlib import Path


def vendor_dir() -> Path:
    return Path(__file__).resolve().parent / "vendor"


def vendor_dll_path() -> Path:
    return vendor_dir() / "FMC006GE31.dll"



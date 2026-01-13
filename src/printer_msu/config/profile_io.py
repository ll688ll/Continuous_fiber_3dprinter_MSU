from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_profile(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8"))


def save_profile(path: str | Path, profile: dict[str, Any]) -> None:
    p = Path(path)
    p.write_text(json.dumps(profile, indent=2, sort_keys=True), encoding="utf-8")



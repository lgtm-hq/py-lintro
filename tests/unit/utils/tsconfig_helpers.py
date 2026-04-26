"""Shared helpers for tsconfig unit tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_tsconfig(path: Path, content: dict[str, Any]) -> Path:
    """Write a tsconfig.json (or any .json) to *path*.

    Args:
        path: Destination path (parent dirs created automatically).
        content: Dict to serialize as JSON.

    Returns:
        The written path.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(content, indent=2), encoding="utf-8")
    return path

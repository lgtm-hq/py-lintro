"""Helpers for loading tool sample fixtures from ``test_samples``."""

from __future__ import annotations

import shutil
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SAMPLES_ROOT = _REPO_ROOT / "test_samples"


def repo_root() -> Path:
    """Return the repository root directory.

    Returns:
        Path: Absolute path to the repository root.
    """
    return _REPO_ROOT


def sample_path(*parts: str) -> Path:
    """Return the absolute path to a fixture under ``test_samples``.

    Args:
        *parts: Path segments relative to ``test_samples``.

    Returns:
        Path: Absolute path to the requested fixture file.
    """
    return _SAMPLES_ROOT.joinpath(*parts)


def copy_sample(
    dest_dir: Path,
    *parts: str,
    dest_name: str | None = None,
) -> Path:
    """Copy a committed sample fixture into a temporary directory.

    Args:
        dest_dir: Directory that should receive the copied fixture.
        *parts: Path segments relative to ``test_samples``.
        dest_name: Optional destination filename; defaults to the source name.

    Returns:
        Path: Absolute path to the copied file in ``dest_dir``.
    """
    src = sample_path(*parts)
    dst = dest_dir / (dest_name or src.name)
    shutil.copy(src, dst)
    return dst

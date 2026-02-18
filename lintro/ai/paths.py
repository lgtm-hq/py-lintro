"""Shared path utilities for AI display rendering.

Provides display-friendly path conversion used across the AI package.
"""

from __future__ import annotations

import os


def relative_path(file_path: str) -> str:
    """Convert a path to be relative to cwd for display.

    Used by display, fix, and interactive modules to show short,
    readable paths instead of absolute ones.

    Args:
        file_path: Absolute or relative file path.

    Returns:
        Relative path string, or the original if conversion fails.
    """
    try:
        return os.path.relpath(file_path)
    except ValueError:
        return file_path

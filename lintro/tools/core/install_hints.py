"""Shared helpers for classifying and locating install commands.

These predicates are consumed by the installer (which executes commands) and
by quick-fix generation (which must only ever suggest commands that can
actually run in the detected environment), so they live in one place.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from lintro.tools.core.manifest_models import ManifestTool

#: Marker the planner uses for script-backed binary installs.
SCRIPT_HINT_PREFIX = "via install-tools.sh"

_MANUAL_HINT_PREFIXES = ("See ", "Install ", "Upgrade ")


def is_manual_hint(hint: str) -> bool:
    """Check whether an install hint is prose rather than a runnable command.

    Args:
        hint: Install/upgrade command string.

    Returns:
        True if the hint requires manual action by a human.
    """
    return (
        hint.startswith(_MANUAL_HINT_PREFIXES)
        or "https://" in hint
        or "http://" in hint
    )


def install_script_path() -> Path | None:
    """Locate ``install-tools.sh`` if it ships with this installation.

    Returns:
        Path to the script, or None when it is not present (pip installs do
        not ship it).
    """
    script = Path(__file__).parents[3] / "scripts" / "utils" / "install-tools.sh"
    return script if script.exists() else None


def has_install_script(tool: ManifestTool) -> bool:
    """Check whether a binary tool can be installed via ``install-tools.sh``.

    Args:
        tool: Tool to check.

    Returns:
        True if the script exists, bash is available, and the tool is a
        binary tool the script can handle.
    """
    if tool.install_type != "binary":
        return False
    if not shutil.which("bash"):
        return False
    return install_script_path() is not None

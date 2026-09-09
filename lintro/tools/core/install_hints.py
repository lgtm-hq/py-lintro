"""Shared helpers for classifying and locating install commands.

These predicates are consumed by the installer (which executes commands) and
by quick-fix generation (which must only ever suggest commands that can
actually run in the detected environment), so they live in one place.
"""

from __future__ import annotations

import shutil
import subprocess  # nosec B404 - used only to ask Homebrew what it manages; shell=False
from pathlib import Path

from lintro.tools.core.manifest_models import ManifestTool

#: Marker the planner uses for script-backed binary installs.
SCRIPT_HINT_PREFIX = "via install-tools.sh"

#: Semgrep lives in an isolated lockfile venv (#2104); never pip into the
#: project environment.
SEMGREP_ISOLATED_INSTALL_HINT = (
    "Install via: ./scripts/utils/install-semgrep.sh "
    "(pinned lockfile), uv tool install semgrep, "
    "or brew install semgrep"
)

#: Checkov requires ``packaging>=23.0,<24.0`` while lintro requires
#: ``packaging>=25.0``, so it can never be pip-installed into the project
#: environment; ``install-tools.sh`` puts it in its own ``uv tool`` venv (#422).
CHECKOV_ISOLATED_INSTALL_HINT = (
    "Install via: uv tool install checkov, or "
    "uv tool install --upgrade checkov when one is already installed "
    "(a plain uv tool install no-ops on an existing tool and would leave a "
    "below-minimum version in place). Isolated venv: checkov pins "
    "packaging<24 and cannot share lintro's environment"
)

#: Tools whose install and upgrade guidance must always point at an isolated
#: venv, whatever install strategy the manifest routes them through. Both
#: entries pin transitive dependencies that conflict with lintro's own, so
#: neither may be installed into the project environment (#2104, #422), and
#: neither publishes the release binaries a ``binary`` strategy would otherwise
#: send a user hunting for.
ISOLATED_INSTALL_HINTS: dict[str, str] = {
    "semgrep": SEMGREP_ISOLATED_INSTALL_HINT,
    "checkov": CHECKOV_ISOLATED_INSTALL_HINT,
}


def isolated_install_hint(tool_name: str) -> str | None:
    """Return the isolated-venv hint for a tool, if it has one.

    Every install strategy that can be routed an isolated tool consults this
    before its own ecosystem branch, so the mapping above stays the single
    source of truth rather than being re-stated per strategy.

    Args:
        tool_name: Canonical lintro tool name.

    Returns:
        The isolated-venv install/upgrade hint, or None when the tool may be
        installed into the ambient environment.
    """
    return ISOLATED_INSTALL_HINTS.get(tool_name)


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


def is_brew_managed(package: str) -> bool:
    """Check whether Homebrew manages a package.

    Shared by the installer (which refuses to run ``brew upgrade`` on a
    formula brew does not own) and quick-fix generation (which must not
    suggest such a command in the first place).

    Args:
        package: Homebrew formula name.

    Returns:
        True if brew manages this package.
    """
    if not shutil.which("brew"):
        return False
    try:
        result = subprocess.run(  # nosec B603 B607 - argv is an internally-built list run with shell=False; binary name resolved from PATH, not attacker-controlled
            ["brew", "list", "--formula", package],
            capture_output=True,
            timeout=10,
            check=False,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False

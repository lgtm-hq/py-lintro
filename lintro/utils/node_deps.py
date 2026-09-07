"""Node.js dependency management utilities.

Provides functions for detecting and installing Node.js dependencies,
primarily used by tools that depend on node_modules (like tsc).
"""

from __future__ import annotations

import os
import shutil
import subprocess  # nosec B404 - used safely with shell disabled
import time
from pathlib import Path

from loguru import logger

from lintro.utils.env import get_subprocess_env


def should_install_deps(cwd: Path) -> bool:
    """Check if Node.js dependencies should be installed.

    Returns True if:
    - package.json exists in the directory
    - node_modules directory is missing or empty

    Args:
        cwd: Directory to check for package.json and node_modules.

    Returns:
        True if dependencies should be installed, False otherwise.

    Raises:
        PermissionError: If the directory is not writable+executable
            (e.g., read-only mount).

    Examples:
        >>> from pathlib import Path
        >>> # In a directory with package.json but no node_modules
        >>> should_install_deps(Path("/project"))  # doctest: +SKIP
        True
    """
    package_json = cwd / "package.json"
    node_modules = cwd / "node_modules"

    if not package_json.exists():
        logger.debug("[node_deps] No package.json found in {}", cwd)
        return False

    if not node_modules.exists():
        # Check if the directory is writable and executable before claiming
        # deps are needed.  On read-only mounts (e.g., -v "...:/code:ro"),
        # bun install would fail with EACCES.
        if not os.access(cwd, os.W_OK | os.X_OK):
            msg = (
                f"Cannot install dependencies: {cwd} is not writable (read-only mount?)"
            )
            logger.warning("[node_deps] {}", msg)
            raise PermissionError(msg)
        logger.debug("[node_deps] node_modules missing in {}", cwd)
        return True

    # Check if node_modules is empty (besides potential .bin directory)
    try:
        for entry in node_modules.iterdir():
            if entry.name != ".bin":
                logger.debug(
                    "[node_deps] Dependencies appear to be installed in {}",
                    cwd,
                )
                return False
        logger.debug("[node_deps] node_modules is effectively empty in {}", cwd)
        return True
    except OSError as e:
        logger.debug("[node_deps] Error checking node_modules: {}", e)
        return True


def get_package_manager_command() -> list[str] | None:
    """Determine which package manager to use for installation.

    Checks for available package managers in order of preference:
    1. bun (fastest)
    2. npm (most common)

    Returns:
        Command list for installation, or None if no package manager found.

    Examples:
        >>> cmd = get_package_manager_command()
        >>> cmd is not None  # doctest: +SKIP
        True
    """
    # Prefer bun for speed
    # --ignore-scripts prevents lifecycle script execution for security
    if shutil.which("bun"):
        return ["bun", "install", "--ignore-scripts"]

    # Fallback to npm
    # --ignore-scripts prevents lifecycle script execution for security
    if shutil.which("npm"):
        return ["npm", "install", "--ignore-scripts"]

    return None


def install_node_deps(
    cwd: Path,
    timeout: int = 120,
) -> tuple[bool, str]:
    """Install Node.js dependencies using the available package manager.

    Attempts to install dependencies using bun or npm, preferring bun
    for speed. Uses frozen lockfile when available, falling back to
    regular install.

    Args:
        cwd: Directory containing package.json where installation should run.
        timeout: Maximum time in seconds to wait for installation.

    Returns:
        Tuple of (success, output) where:
        - success: True if installation completed successfully
        - output: Combined stdout/stderr from the installation command

    Examples:
        >>> from pathlib import Path
        >>> success, output = install_node_deps(Path("/project"))  # doctest: +SKIP
        >>> success
        True
    """
    # First check if we should install
    try:
        if not should_install_deps(cwd):
            return True, "Dependencies already installed"
    except PermissionError as e:
        return False, str(e)

    # Get the package manager command
    base_cmd = get_package_manager_command()
    if not base_cmd:
        return False, (
            "No package manager found. Please install bun or npm.\n"
            "  - Install bun: curl -fsSL https://bun.sh/install | bash\n"
            "  - Install npm: https://nodejs.org/"
        )

    manager_name = base_cmd[0]
    logger.info("[node_deps] Installing dependencies with {} in {}", manager_name, cwd)

    run_env = get_subprocess_env()

    # Try with frozen lockfile first (for CI reproducibility)
    frozen_cmd = _get_frozen_install_cmd(base_cmd)
    start_time = time.monotonic()

    try:
        result = subprocess.run(  # nosec B603 - command is constructed safely
            frozen_cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
            env=run_env,
        )

        if result.returncode == 0:
            output = result.stdout + result.stderr
            logger.info("[node_deps] Dependencies installed successfully")
            return True, output.strip()

        # Frozen install failed, try regular install
        logger.debug(
            "[node_deps] Frozen install failed, trying regular install: {}",
            result.stderr,
        )

    except subprocess.TimeoutExpired:
        logger.warning("[node_deps] Frozen install timed out, trying regular install")
    except OSError as e:
        logger.debug("[node_deps] Frozen install failed with OS error: {}", e)

    # Calculate remaining timeout for fallback
    elapsed = time.monotonic() - start_time
    remaining_timeout = max(0, timeout - elapsed)

    if remaining_timeout <= 0:
        return False, f"Installation timed out after {timeout} seconds"

    # Fallback to regular install (without frozen lockfile)
    try:
        result = subprocess.run(  # nosec B603 - command is constructed safely
            base_cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=remaining_timeout,
            shell=False,
            env=run_env,
        )

        output = result.stdout + result.stderr

        if result.returncode == 0:
            logger.info("[node_deps] Dependencies installed successfully")
            return True, output.strip()

        logger.error("[node_deps] Installation failed: {}", result.stderr)
        return False, output.strip()

    except subprocess.TimeoutExpired:
        return False, f"Installation timed out after {timeout} seconds"
    except OSError as e:
        return False, f"Installation failed: {e}"


def _get_frozen_install_cmd(base_cmd: list[str]) -> list[str]:
    """Get the frozen lockfile install command for a package manager.

    Args:
        base_cmd: Base installation command
            (e.g., ["bun", "install", "--ignore-scripts"]).

    Returns:
        Command with frozen lockfile flag added.

    Raises:
        ValueError: If base_cmd is empty.
    """
    if not base_cmd:
        raise ValueError("base_cmd cannot be empty")

    manager = base_cmd[0]

    if manager == "bun":
        return [*base_cmd, "--frozen-lockfile"]
    if manager == "npm":
        # npm ci is the equivalent of frozen lockfile for npm
        # Preserve any flags from base_cmd (e.g., --ignore-scripts)
        extra_flags = base_cmd[2:]  # Skip "npm" and "install"
        return ["npm", "ci", *extra_flags]

    return base_cmd

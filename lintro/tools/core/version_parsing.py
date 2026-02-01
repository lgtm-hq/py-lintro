"""Version parsing utilities for tool version checking and validation."""

import re
import subprocess  # nosec B404 - used safely with shell disabled
from dataclasses import dataclass, field
from functools import lru_cache

from loguru import logger
from packaging.version import InvalidVersion, Version

from lintro.enums.tool_name import ToolName, normalize_tool_name

# Import actual implementations from version_checking with aliases
# to avoid name conflicts
from lintro.tools.core.version_checking import (
    VERSION_CHECK_TIMEOUT,
)
from lintro.tools.core.version_checking import (
    get_install_hints as _get_install_hints_impl,
)
from lintro.tools.core.version_checking import (
    get_minimum_versions as _get_minimum_versions_impl,
)

# Sentinel value for unknown/unspecified version requirements
VERSION_UNKNOWN: str = "unknown"

# Common regex pattern for tools that output simple version numbers
# Matches version strings like "1.2.3", "0.14.0", "25.1", etc.
VERSION_NUMBER_PATTERN: str = r"(\d+(?:\.\d+)*)"

# Tools that use the simple version number pattern
TOOLS_WITH_SIMPLE_VERSION_PATTERN: set[ToolName] = {
    ToolName.BANDIT,
    ToolName.CARGO_AUDIT,
    ToolName.CARGO_DENY,
    ToolName.GITLEAKS,
    ToolName.HADOLINT,
    ToolName.PYDOCLINT,
    ToolName.ACTIONLINT,
    ToolName.OXFMT,
    ToolName.OXLINT,
    ToolName.PRETTIER,
    ToolName.RUSTFMT,
    ToolName.SEMGREP,
    ToolName.SHELLCHECK,
    ToolName.SHFMT,
    ToolName.SQLFLUFF,
    ToolName.TAPLO,
}


@lru_cache(maxsize=1)
def _get_minimum_versions_cached() -> dict[str, str]:
    """Get minimum version requirements (cached).

    Returns:
        dict[str, str]: Dictionary mapping tool names to minimum version strings.
    """
    # Call the imported implementation directly to avoid recursion
    return _get_minimum_versions_impl()


@lru_cache(maxsize=1)
def _get_install_hints_cached() -> dict[str, str]:
    """Get installation hints (cached).

    Returns:
        dict[str, str]: Dictionary mapping tool names to installation hint strings.
    """
    # Call the imported implementation directly to avoid recursion
    return _get_install_hints_impl()


def get_minimum_versions() -> dict[str, str]:
    """Get minimum version requirements for all tools.

    Returns:
        dict[str, str]: Dictionary mapping tool names to minimum version strings.
    """
    # Return a copy to avoid sharing mutable state
    return dict(_get_minimum_versions_cached())


def get_install_hints() -> dict[str, str]:
    """Get installation hints for tools that don't meet requirements.

    Returns:
        dict[str, str]: Dictionary mapping tool names to installation hint strings.
    """
    # Return a copy to avoid sharing mutable state
    return dict(_get_install_hints_cached())


@dataclass
class ToolVersionInfo:
    """Information about a tool's version requirements."""

    name: str = field(default="")
    min_version: str = field(default="")
    install_hint: str = field(default="")
    current_version: str | None = field(default=None)
    version_check_passed: bool = field(default=False)
    error_message: str | None = field(default=None)


def parse_version(version_str: str) -> Version:
    """Parse a version string into a comparable Version object.

    Uses the standard packaging library for robust version parsing that
    handles PEP 440 compliant versions including pre-release, post-release,
    and development versions.

    Args:
        version_str: Version string like "1.2.3", "0.14.0", or "v1.0.0-alpha"

    Returns:
        Version: Comparable Version object from packaging library.

    Raises:
        ValueError: If the version string cannot be parsed.

    Examples:
        >>> parse_version("1.2.3")
        <Version('1.2.3')>
        >>> parse_version("v0.14.0")
        <Version('0.14.0')>
    """
    # Strip common prefixes and suffixes that packaging can't handle
    cleaned = version_str.strip()

    # Handle optional leading 'v' (e.g., "v1.2.3")
    if cleaned.lower().startswith("v"):
        cleaned = cleaned[1:]

    # Handle pre-release suffixes with hyphens (convert to PEP 440 format)
    # e.g., "1.0.0-alpha" -> "1.0.0a0", "1.0.0-beta.1" -> "1.0.0b1"
    cleaned = cleaned.split("+")[0]  # Remove build metadata
    if "-" in cleaned:
        base, suffix = cleaned.split("-", 1)
        # Try to use just the base version for simpler comparison
        cleaned = base

    try:
        return Version(cleaned)
    except InvalidVersion as e:
        raise ValueError(f"Unable to parse version string: {version_str!r}") from e


def compare_versions(version1: str, version2: str) -> int:
    """Compare two version strings.

    Uses the packaging library for robust version comparison that properly
    handles major/minor/patch versions, pre-release versions, and more.

    Args:
        version1: First version string
        version2: Second version string

    Returns:
        int: -1 if version1 < version2, 0 if equal, 1 if version1 > version2

    Examples:
        >>> compare_versions("1.2.3", "1.2.3")
        0
        >>> compare_versions("1.2.3", "1.2.4")
        -1
        >>> compare_versions("2.0.0", "1.9.9")
        1
    """
    v1 = parse_version(version1)
    v2 = parse_version(version2)
    return (v1 > v2) - (v1 < v2)


def check_tool_version(tool_name: str, command: list[str]) -> ToolVersionInfo:
    """Check if a tool meets minimum version requirements.

    Args:
        tool_name: Name of the tool to check
        command: Command list to run the tool (e.g., ["python", "-m", "ruff"])

    Returns:
        ToolVersionInfo: Version check results
    """
    minimum_versions = get_minimum_versions()
    install_hints = get_install_hints()

    min_version = minimum_versions.get(tool_name, VERSION_UNKNOWN)
    install_hint = install_hints.get(
        tool_name,
        f"Install {tool_name} and ensure it's in PATH",
    )
    has_requirements = tool_name in minimum_versions

    info = ToolVersionInfo(
        name=tool_name,
        min_version=min_version,
        install_hint=install_hint,
        # If no requirements, assume check passes
        version_check_passed=not has_requirements,
    )

    try:
        # Run the tool with --version flag
        version_cmd = command + ["--version"]
        result = subprocess.run(  # nosec B603 - args list, shell=False
            version_cmd,
            capture_output=True,
            text=True,
            timeout=VERSION_CHECK_TIMEOUT,  # Configurable version check timeout
        )

        if result.returncode != 0:
            info.error_message = f"Command failed: {' '.join(version_cmd)}"
            logger.debug(
                f"[VersionCheck] Failed to get version for {tool_name}: "
                f"{info.error_message}",
            )
            return info

        # Extract version from output
        output = result.stdout + result.stderr
        info.current_version = extract_version_from_output(output, tool_name)

        if not info.current_version:
            info.error_message = (
                f"Could not parse version from output: {output.strip()}"
            )
            logger.debug(
                f"[VersionCheck] Failed to parse version for {tool_name}: "
                f"{info.error_message}",
            )
            return info

        # Compare versions
        if min_version != VERSION_UNKNOWN:
            comparison = compare_versions(info.current_version, min_version)
            info.version_check_passed = comparison >= 0
        else:
            # If min_version is unknown, consider check passed since we got a version
            info.version_check_passed = True

        if not info.version_check_passed:
            info.error_message = (
                f"Version {info.current_version} is below minimum requirement "
                f"{min_version}"
            )
            logger.debug(
                f"[VersionCheck] Version check failed for {tool_name}: "
                f"{info.error_message}",
            )

    except (subprocess.TimeoutExpired, OSError) as e:
        info.error_message = f"Failed to run version check: {e}"
        logger.debug(f"[VersionCheck] Exception checking version for {tool_name}: {e}")

    return info


def extract_version_from_output(output: str, tool_name: str | ToolName) -> str | None:
    """Extract version string from tool --version output.

    Args:
        output: Raw output from tool --version
        tool_name: Name of the tool (to handle tool-specific parsing)

    Returns:
        Optional[str]: Extracted version string, or None if not found
    """
    output = output.strip()
    tool_name = normalize_tool_name(tool_name)

    # Tool-specific patterns first (most reliable)
    if tool_name == ToolName.BLACK:
        # black: "black, 25.9.0 (compiled: yes)"
        match = re.search(r"black,\s+(\d+(?:\.\d+)*)", output, re.IGNORECASE)
        if match:
            return match.group(1)

    elif tool_name in TOOLS_WITH_SIMPLE_VERSION_PATTERN:
        # Tools with simple version output (see TOOLS_WITH_SIMPLE_VERSION_PATTERN)
        match = re.search(VERSION_NUMBER_PATTERN, output)
        if match:
            return match.group(1)

    elif tool_name == ToolName.MARKDOWNLINT:
        # markdownlint-cli2: "markdownlint-cli2 v0.19.1 (markdownlint v0.39.0)"
        # Extract the cli2 version (first version number after "v")
        match = re.search(
            r"markdownlint-cli2\s+v(\d+(?:\.\d+)*)",
            output,
            re.IGNORECASE,
        )
        if match:
            return match.group(1)
        # Fallback: look for any version pattern
        match = re.search(r"v(\d+(?:\.\d+)+)", output)
        if match:
            return match.group(1)

    elif tool_name == ToolName.CLIPPY:
        # For clippy, we check Rust version instead (clippy is tied to Rust)
        # rustc --version outputs: "rustc 1.92.0 (ded5c06cf 2025-12-08)"
        # cargo clippy --version outputs: "clippy 0.1.92 (ded5c06cf2 2025-12-08)"
        # Extract Rust version from rustc output
        match = re.search(r"rustc\s+(\d+(?:\.\d+)*)", output, re.IGNORECASE)
        if match:
            return match.group(1)
        # Fallback: try clippy version format (0.1.X -> 1.X.0)
        # Clippy uses 0.1.X where X is the Rust minor version
        match = re.search(r"clippy\s+0\.1\.(\d+)", output, re.IGNORECASE)
        if match:
            return f"1.{match.group(1)}.0"

    # Fallback: look for version-like pattern (more restrictive)
    # Match version numbers that look reasonable: 1.2.3, 0.14, 25.1, etc.
    match = re.search(r"\b(\d+(?:\.\d+){0,3})\b", output)
    if match:
        return match.group(1)

    return None

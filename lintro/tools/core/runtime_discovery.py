"""Runtime tool discovery for compiled binary mode.

This module provides functions to discover external tools (ruff, black, mypy, etc.)
at runtime by searching the system PATH. This is essential for the compiled binary
distribution where lintro itself is standalone but depends on external tools.

When lintro runs as a compiled binary:
1. It cannot bundle tools like ruff, mypy, black (they are separate executables)
2. Users must install these tools separately (via pip, brew, etc.)
3. This module discovers which tools are available and their locations

Usage:
    from lintro.tools.core.runtime_discovery import discover_tool, discover_all_tools

    # Discover a single tool
    tool = discover_tool("ruff")
    if tool.available:
        print(f"Found ruff at {tool.path} (version {tool.version})")

    # Discover all configured tools
    tools = discover_all_tools()
    for name, info in tools.items():
        print(f"{name}: {'available' if info.available else 'missing'}")
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess  # nosec B404 - subprocess is the core mechanism for invoking external tools; all invocations use shell=False
import threading
from dataclasses import dataclass, field

from loguru import logger

# Default timeout for version checks (seconds)
VERSION_CHECK_TIMEOUT: int = 5


@dataclass
class _ToolProbeInfo:
    """Internal probe metadata for a single tool."""

    version_command: tuple[str, ...]
    executable: str | None = None


def _get_tool_probe_info() -> dict[str, _ToolProbeInfo] | None:
    """Get tool probe info (version commands + preferred executable) from the registry.

    Returns:
        Dict mapping tool names to probe info,
        or None if the registry is unavailable (early startup).
    """
    try:
        from lintro.tools.core.tool_registry import ManifestRegistry

        registry = ManifestRegistry.load()
        return {
            tool.name: _ToolProbeInfo(
                version_command=tool.version_command,
                executable=tool.install_bin,
            )
            for tool in registry.all_tools(include_dev=True)
            if tool.version_command
        }
    except (
        ImportError,
        FileNotFoundError,
        KeyError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        logger.debug("Registry unavailable, tool discovery limited: {}", exc)
        return None


@dataclass
class DiscoveredTool:
    """Information about a discovered external tool.

    Attributes:
        name: The canonical name of the tool (e.g., "ruff", "black").
        path: Full path to the tool executable, or empty string if not found.
        version: Version string if available, None otherwise.
        available: True if the tool was found and is executable, False by default.
        error_message: Error message if discovery failed, None otherwise.
    """

    name: str
    path: str = ""
    version: str | None = None
    available: bool = False
    error_message: str | None = None


@dataclass
class ToolDiscoveryCache:
    """Cache for discovered tools to avoid repeated PATH lookups.

    Attributes:
        tools: Dictionary mapping tool names to their discovery info.
        is_populated: True if the cache has been populated.
    """

    tools: dict[str, DiscoveredTool] = field(default_factory=dict)
    is_populated: bool = False


# Global cache instance and lock for thread safety
_discovery_cache = ToolDiscoveryCache()
_discovery_cache_lock = threading.Lock()


def _extract_version(output: str) -> str | None:
    """Extract version number from tool output.

    Args:
        output: Raw output from tool's version command.

    Returns:
        Extracted version string or None if not found.
    """
    # Common version patterns:
    # - "ruff 0.1.0"
    # - "black, version 23.0.0"
    # - "mypy 1.0.0"
    # - "v1.2.3"
    patterns = [
        r"(\d+\.\d+\.\d+)",  # Semantic version (1.2.3)
        r"v(\d+\.\d+\.\d+)",  # Prefixed version (v1.2.3)
        r"version\s+(\d+\.\d+\.\d+)",  # "version 1.2.3"
    ]

    for pattern in patterns:
        match = re.search(pattern, output, re.IGNORECASE)
        if match:
            return match.group(1)

    return None


def discover_tool(
    tool_name: str,
    use_cache: bool = True,
    *,
    _probe_info: dict[str, _ToolProbeInfo] | None = None,
) -> DiscoveredTool:
    """Discover a single tool in the system PATH.

    Thread-safe: uses lock to protect cache access.

    Args:
        tool_name: Name of the tool to discover (e.g., "ruff", "black").
        use_cache: Whether to use cached results if available.
        _probe_info: Pre-loaded probe info map (avoids redundant registry
            lookups when called from discover_all_tools).

    Returns:
        DiscoveredTool with information about the tool.
    """
    # Check cache first (thread-safe)
    with _discovery_cache_lock:
        if use_cache and tool_name in _discovery_cache.tools:
            return _discovery_cache.tools[tool_name]

    logger.debug(f"Discovering tool: {tool_name}")

    # Get probe metadata from the registry (version_command + preferred executable)
    probe_info_map = _probe_info if _probe_info is not None else _get_tool_probe_info()
    if probe_info_map is None:
        # Registry unavailable — don't cache guessed probes so we retry later
        logger.debug(f"Registry unavailable, skipping discovery for {tool_name}")
        return DiscoveredTool(
            name=tool_name,
            available=False,
            error_message="registry unavailable",
        )

    probe = probe_info_map.get(tool_name)
    version_cmd = probe.version_command if probe else [tool_name, "--version"]

    # Prefer the registry-provided executable (install_bin) over deriving
    # from version_cmd[0], which misreports tools invoked via wrappers
    # (e.g., cargo subcommands, node -e probes).
    if probe and probe.executable:
        executable = probe.executable
    else:
        executable = version_cmd[0] if version_cmd else tool_name
        # For shell/interpreter-wrapped probes (e.g., ["sh","-c","..."],
        # ["node","-e","..."], ["python","-c","..."]), resolve the inner
        # command's executable instead of caching the wrapper path.
        _wrappers = ("sh", "bash", "zsh", "node", "python", "python3", "ruby", "perl")
        if (
            executable in _wrappers
            and len(version_cmd) >= 3
            and version_cmd[1] in ("-c", "-e")
        ):
            inner_tokens = version_cmd[2].split()
            if inner_tokens:
                executable = inner_tokens[0]

    # Find the executable in PATH (outside lock - this is IO-bound)
    path = shutil.which(executable)

    if not path:
        result = DiscoveredTool(
            name=tool_name,
            path="",
            available=False,
            error_message=f"{executable} not found in PATH",
        )
        with _discovery_cache_lock:
            _discovery_cache.tools[tool_name] = result
        logger.debug(f"Tool {tool_name} ({executable}) not found in PATH")
        return result

    # Run the full version probe — only mark available if it succeeds
    version: str | None = None
    probe_ok = False

    try:
        proc_result = subprocess.run(  # nosec B603 - argv is an internally-built list run with shell=False; binary resolved from a known command, no user shell input
            version_cmd,
            capture_output=True,
            text=True,
            timeout=VERSION_CHECK_TIMEOUT,
        )
        if proc_result.returncode == 0:
            version = _extract_version(proc_result.stdout or proc_result.stderr)
            probe_ok = True
    except subprocess.TimeoutExpired:
        logger.debug(f"Version check for {tool_name} timed out")
    except (OSError, subprocess.SubprocessError) as e:
        logger.debug(f"Failed to get version for {tool_name}: {e}")

    discovered = DiscoveredTool(
        name=tool_name,
        path=path,
        version=version,
        available=probe_ok,
        error_message=None if probe_ok else f"{tool_name} version probe failed",
    )

    with _discovery_cache_lock:
        _discovery_cache.tools[tool_name] = discovered
    logger.debug(f"Discovered {tool_name} at {path} (version: {version})")

    return discovered


def discover_all_tools(use_cache: bool = True) -> dict[str, DiscoveredTool]:
    """Discover all configured external tools.

    Thread-safe: uses lock to protect cache access.

    Args:
        use_cache: Whether to use cached results if available.

    Returns:
        Dictionary mapping tool names to their discovery info.
    """
    with _discovery_cache_lock:
        if use_cache and _discovery_cache.is_populated:
            return _discovery_cache.tools.copy()

    probe_info_map = _get_tool_probe_info()
    if probe_info_map is None:
        # Registry unavailable — clear stale discoveries so downstream
        # get_available_tools()/get_unavailable_tools() don't see outdated entries
        with _discovery_cache_lock:
            _discovery_cache.tools.clear()
            _discovery_cache.is_populated = False
        return {}

    for tool_name in probe_info_map:
        discover_tool(tool_name, use_cache=False, _probe_info=probe_info_map)

    with _discovery_cache_lock:
        # Prune stale entries for tools no longer in the registry
        stale = _discovery_cache.tools.keys() - probe_info_map.keys()
        for key in stale:
            del _discovery_cache.tools[key]
        _discovery_cache.is_populated = True
        return _discovery_cache.tools.copy()


def clear_discovery_cache() -> None:
    """Clear the tool discovery cache.

    Thread-safe: uses lock to protect cache access.
    Call this if tools may have been installed/uninstalled since last check.
    """
    global _discovery_cache
    with _discovery_cache_lock:
        _discovery_cache = ToolDiscoveryCache()
    logger.debug("Tool discovery cache cleared")


def is_tool_available(tool_name: str) -> bool:
    """Check if a tool is available in the system PATH.

    Args:
        tool_name: Name of the tool to check.

    Returns:
        True if the tool is available, False otherwise.
    """
    return discover_tool(tool_name).available


def get_tool_path(tool_name: str) -> str | None:
    """Get the full path to a tool executable.

    Args:
        tool_name: Name of the tool.

    Returns:
        Full path to the executable, or None if not found.
    """
    tool = discover_tool(tool_name)
    return tool.path if tool.available else None


def get_unavailable_tools() -> list[str]:
    """Get a list of tools that are not available.

    Returns:
        List of tool names that were not found in PATH.
    """
    discover_all_tools()
    return [name for name, tool in _discovery_cache.tools.items() if not tool.available]


def get_available_tools() -> list[str]:
    """Get a list of tools that are available.

    Returns:
        List of tool names that were found in PATH.
    """
    discover_all_tools()
    return [name for name, tool in _discovery_cache.tools.items() if tool.available]


def format_tool_status_table() -> str:
    """Format a table showing the status of all tools.

    Returns:
        Formatted string table showing tool availability.
    """
    tools = discover_all_tools()

    lines = [
        "Tool Discovery Status",
        "=" * 60,
        f"{'Tool':<15} {'Status':<12} {'Version':<15} {'Path'}",
        "-" * 60,
    ]

    for name, tool in sorted(tools.items()):
        status = "Available" if tool.available else "Missing"
        version = tool.version or "-"
        path = tool.path or tool.error_message or "-"
        lines.append(f"{name:<15} {status:<12} {version:<15} {path}")

    lines.append("-" * 60)

    available = sum(1 for t in tools.values() if t.available)
    total = len(tools)
    lines.append(f"Available: {available}/{total} tools")

    return "\n".join(lines)

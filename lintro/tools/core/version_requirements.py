"""Tool version requirements checking utilities."""

from __future__ import annotations

from loguru import logger

from lintro.tools.core.tool_registry import ToolRegistry
from lintro.tools.core.version_checking import (
    get_install_hints,
    get_minimum_versions,
)
from lintro.tools.core.version_parsing import (
    ToolVersionInfo,
    check_tool_version,
    compare_versions,
    extract_version_from_output,
    parse_version,
)

__all__ = [
    "ToolVersionInfo",
    "check_tool_version",
    "get_all_tool_versions",
    "compare_versions",
    "extract_version_from_output",
    "get_install_hints",
    "get_minimum_versions",
    "parse_version",
]


def get_all_tool_versions() -> dict[str, ToolVersionInfo]:
    """Get version information for all supported tools.

    Uses the unified ToolRegistry (manifest.json) as the single source of truth
    for tool commands and version requirements.

    Returns:
        dict[str, ToolVersionInfo]: Dictionary mapping tool names to version info.
    """
    registry = ToolRegistry.load()
    results = {}
    minimum_versions = get_minimum_versions()
    install_hints = get_install_hints()

    for tool in registry.all_tools(include_dev=True):
        if not tool.version_command:
            continue

        # The manifest's version_command is the complete probe command;
        # never append an extra --version flag.
        try:
            results[tool.name] = check_tool_version(
                tool.name,
                list(tool.version_command),
                append_version=False,
            )
        except (OSError, ValueError, RuntimeError) as e:
            logger.debug(f"Failed to check version for {tool.name}: {e}")
            normalized_key = tool.name.replace("-", "_")
            min_version = minimum_versions.get(
                tool.name,
                minimum_versions.get(normalized_key, "unknown"),
            )
            install_hint = install_hints.get(
                tool.name,
                install_hints.get(normalized_key, f"Install {tool.name}"),
            )
            results[tool.name] = ToolVersionInfo(
                name=tool.name,
                min_version=min_version,
                install_hint=install_hint,
                error_message=f"Failed to check version: {e}",
            )

    return results

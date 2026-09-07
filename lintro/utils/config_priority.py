"""Configuration precedence helpers for Lintro.

This module resolves effective configuration values across the config
sources. Execution order is *not* configuration: it is derived from declared
tool claims by :mod:`lintro.tools.core.scheduler` (#1742).
"""

from __future__ import annotations

from typing import Any

from lintro.utils.config import (
    load_lintro_global_config,
    load_lintro_tool_config,
    load_pyproject,
)
from lintro.utils.config_constants import GLOBAL_SETTINGS
from lintro.utils.native_parsers import _load_native_tool_config


def _get_nested_value(config: dict[str, Any], key_path: str) -> Any:
    """Get a nested value from a config dict using dot notation.

    Args:
        config: Configuration dictionary.
        key_path: Dot-separated key path (e.g., "line-length.max").

    Returns:
        Value at path, or None if not found.
    """
    keys = key_path.split(".")
    current = config
    for key in keys:
        if isinstance(current, dict) and key in current:
            current = current[key]
        else:
            return None
    return current


def get_effective_line_length(tool_name: str) -> int | None:
    """Get the effective line length for a specific tool.

    Priority:
    1. [tool.lintro.<tool>] line_length
    2. [tool.lintro] line_length
    3. [tool.ruff] line-length (as fallback source of truth)
    4. Native tool config
    5. None (use tool default)

    Args:
        tool_name: Name of the tool.

    Returns:
        Effective line length, or None to use tool default.
    """
    # 1. Check tool-specific lintro config
    lintro_tool = load_lintro_tool_config(tool_name)
    if "line_length" in lintro_tool and isinstance(lintro_tool["line_length"], int):
        return lintro_tool["line_length"]
    if "line-length" in lintro_tool and isinstance(lintro_tool["line-length"], int):
        return lintro_tool["line-length"]

    # 2. Check global lintro config
    lintro_global = load_lintro_global_config()
    if "line_length" in lintro_global and isinstance(
        lintro_global["line_length"],
        int,
    ):
        return lintro_global["line_length"]
    if "line-length" in lintro_global and isinstance(
        lintro_global["line-length"],
        int,
    ):
        return lintro_global["line-length"]

    # 3. Fall back to Ruff's line-length as source of truth
    pyproject = load_pyproject()
    tool_section_raw = pyproject.get("tool", {})
    tool_section = tool_section_raw if isinstance(tool_section_raw, dict) else {}
    ruff_config_raw = tool_section.get("ruff", {})
    ruff_config = ruff_config_raw if isinstance(ruff_config_raw, dict) else {}
    if "line-length" in ruff_config and isinstance(ruff_config["line-length"], int):
        return ruff_config["line-length"]
    if "line_length" in ruff_config and isinstance(ruff_config["line_length"], int):
        return ruff_config["line_length"]

    # 4. Check native tool config (for non-Ruff tools)
    native = _load_native_tool_config(tool_name)
    setting_key = GLOBAL_SETTINGS.get("line_length", {}).get("tools", {}).get(tool_name)
    if setting_key:
        native_value = _get_nested_value(native, setting_key)
        if isinstance(native_value, int):
            return native_value

    return None

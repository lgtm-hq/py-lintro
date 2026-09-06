"""Ruff command building utilities.

Functions for building ruff check and format command line arguments.
"""

import os
from typing import TYPE_CHECKING, Any

from lintro.enums.env_bool import EnvBool

if TYPE_CHECKING:
    from lintro.tools.ruff.definition import RuffPlugin


def _get_list_option(options: dict[str, Any], key: str) -> list[str]:
    """Get a list option from options dict, returning empty list if not set.

    Args:
        options: Dictionary of options to retrieve from.
        key: Key to look up in the options dictionary.

    Returns:
        List of string values, or empty list if key not found.
    """
    value = options.get(key)
    if value is None:
        return []
    # Handle single string value
    if isinstance(value, str):
        return [value]
    # Handle list, tuple, set, or other iterables
    try:
        return [str(item) for item in value]
    except TypeError:
        # Non-iterable scalar value
        return [str(value)]


def _get_set_option(options: dict[str, Any], key: str) -> set[str]:
    """Get a set option from options dict, returning empty set if not set.

    Args:
        options: dict[str, Any]: Dictionary of options to retrieve from.
        key: str: Key to look up in the options dictionary.

    Returns:
        set[str]: Set of string values, or empty set if key not found.
    """
    return set(_get_list_option(options, key))


# Constants from tool_ruff.py
RUFF_OUTPUT_FORMAT: str = "json"


def build_ruff_check_command(
    tool: "RuffPlugin",
    files: list[str],
    fix: bool = False,
) -> list[str]:
    """Build the ruff check command.

    Args:
        tool: RuffTool instance
        files: list[str]: List of files to check.
        fix: bool: Whether to apply fixes.

    Returns:
        list[str]: List of command arguments.
    """
    cmd: list[str] = tool._get_executable_command(tool_name="ruff") + ["check"]

    # Get enforced settings to avoid duplicate CLI args
    enforced = tool._get_enforced_settings()

    # Add Lintro config injection args (--line-length, --target-version)
    # from enforce tier. This takes precedence over native config and options
    config_args = tool._build_config_args()
    if config_args:
        cmd.extend(config_args)
    # Add --isolated if in test mode (fallback when no Lintro config)
    elif os.environ.get("LINTRO_TEST_MODE") == EnvBool.TRUE:
        cmd.append("--isolated")

    # Add configuration options
    selected_rules = _get_list_option(tool.options, "select")
    ignored_rules = _get_set_option(tool.options, "ignore")
    extend_selected_rules = _get_list_option(tool.options, "extend_select")

    # Ensure E501 is included when selecting E-family
    # Check in selected_rules, extend_selected_rules, and ignored_rules
    has_e_family = ("E" in selected_rules) or ("E" in extend_selected_rules)

    # Add E501 when E-family is present and E501 not already ignored
    # or in selected/extend
    if (
        has_e_family
        and "E501" not in ignored_rules
        and "E501" not in selected_rules
        and "E501" not in extend_selected_rules
    ):
        extend_selected_rules.append("E501")

    if selected_rules:
        cmd.extend(["--select", ",".join(selected_rules)])
    if ignored_rules:
        cmd.extend(["--ignore", ",".join(sorted(ignored_rules))])
    if extend_selected_rules:
        cmd.extend(["--extend-select", ",".join(extend_selected_rules)])
    extend_ignored_rules = _get_list_option(tool.options, "extend_ignore")
    if extend_ignored_rules:
        cmd.extend(["--extend-ignore", ",".join(extend_ignored_rules)])
    # Only add line_length/target_version from options if not enforced.
    # Note: enforced uses Lintro's generic names (line_length, target_python)
    # while options use tool-specific names (line_length, target_version).
    if tool.options.get("line_length") and "line_length" not in enforced:
        cmd.extend(["--line-length", str(tool.options["line_length"])])
    if tool.options.get("target_version") and "target_python" not in enforced:
        cmd.extend(["--target-version", str(tool.options["target_version"])])

    # Fix options
    if fix:
        cmd.append("--fix")
        if tool.options.get("unsafe_fixes"):
            cmd.append("--unsafe-fixes")
        if tool.options.get("show_fixes"):
            cmd.append("--show-fixes")
        if tool.options.get("fix_only"):
            cmd.append("--fix-only")

    # Output format
    cmd.extend(["--output-format", RUFF_OUTPUT_FORMAT])

    # Add files
    cmd.extend(files)

    return cmd


def build_ruff_format_command(
    tool: "RuffPlugin",
    files: list[str],
    check_only: bool = False,
) -> list[str]:
    """Build the ruff format command.

    Args:
        tool: RuffTool instance
        files: list[str]: List of files to format.
        check_only: bool: Whether to only check formatting without applying changes.

    Returns:
        list[str]: List of command arguments.
    """
    cmd: list[str] = tool._get_executable_command(tool_name="ruff") + ["format"]

    if check_only:
        cmd.append("--check")

    # Add Lintro config injection args (--isolated, --config)
    config_args = tool._build_config_args()
    if config_args:
        cmd.extend(config_args)
    else:
        # Fallback to options-based configuration
        if tool.options.get("line_length"):
            cmd.extend(["--line-length", str(tool.options["line_length"])])
        if tool.options.get("target_version"):
            cmd.extend(["--target-version", str(tool.options["target_version"])])

    # Add files
    cmd.extend(files)

    return cmd

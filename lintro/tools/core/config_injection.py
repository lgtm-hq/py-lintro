"""Configuration injection helpers for BaseTool.

Provides helper functions for tools to inject Lintro configuration
into their CLI arguments.
"""

from __future__ import annotations

from pathlib import Path

from lintro.config.config_loader import get_config
from lintro.config.lintro_config import LintroConfig
from lintro.config.tool_config_generator import (
    generate_defaults_config,
    get_defaults_injection_args,
    get_enforce_cli_args,
)


def _get_lintro_config() -> LintroConfig:
    """Get the current Lintro configuration.

    Returns:
        LintroConfig: Current Lintro configuration instance.
    """
    return get_config()


def _get_enforced_settings(
    lintro_config: LintroConfig | None = None,
) -> dict[str, object]:
    """Get enforced settings as a dictionary.

    Args:
        lintro_config: Optional Lintro config. If None, loads current config.

    Returns:
        dict[str, object]: Dictionary of enforced settings.
    """
    if lintro_config is None:
        lintro_config = _get_lintro_config()

    settings: dict[str, object] = {}
    if lintro_config.enforce.line_length is not None:
        settings["line_length"] = lintro_config.enforce.line_length
    if lintro_config.enforce.target_python is not None:
        settings["target_python"] = lintro_config.enforce.target_python

    return settings


def _get_enforce_cli_args(
    tool_name: str,
    lintro_config: LintroConfig | None = None,
) -> list[str]:
    """Get CLI arguments for enforced settings.

    Args:
        tool_name: Name of the tool.
        lintro_config: Optional Lintro config. If None, loads current config.

    Returns:
        list[str]: CLI arguments to inject enforced settings.
    """
    if lintro_config is None:
        lintro_config = _get_lintro_config()

    args: list[str] = get_enforce_cli_args(
        tool_name=tool_name,
        lintro_config=lintro_config,
    )
    return args


def _get_defaults_config_args(
    tool_name: str,
    lintro_config: LintroConfig | None = None,
) -> list[str]:
    """Get CLI arguments for defaults config injection.

    Args:
        tool_name: Name of the tool.
        lintro_config: Optional Lintro config. If None, loads current config.

    Returns:
        list[str]: CLI arguments to inject defaults config file.
    """
    if lintro_config is None:
        lintro_config = _get_lintro_config()

    # Generate defaults config file if needed
    config_path: Path | None = generate_defaults_config(
        tool_name=tool_name,
        lintro_config=lintro_config,
    )

    args: list[str] = get_defaults_injection_args(
        tool_name=tool_name,
        config_path=config_path,
    )
    return args


def _should_use_lintro_config(
    tool_name: str,
    lintro_config: LintroConfig | None = None,
) -> bool:
    """Check if Lintro config should be used for this tool.

    Args:
        tool_name: Name of the tool.
        lintro_config: Optional Lintro config. If None, loads current config.

    Returns:
        bool: True if Lintro config should be injected.
    """
    if lintro_config is None:
        lintro_config = _get_lintro_config()

    # Check if enforce settings are configured
    if lintro_config.enforce.line_length is not None:
        return True
    if lintro_config.enforce.target_python is not None:
        return True

    # Check if defaults are configured for this tool
    defaults = lintro_config.get_tool_defaults(tool_name.lower())
    if defaults:
        return True

    # Check if builtin defaults exist for this tool
    from lintro.config.tool_config_generator import TOOL_BUILTIN_DEFAULTS

    return tool_name.lower() in TOOL_BUILTIN_DEFAULTS


def _build_config_args(
    tool_name: str,
    lintro_config: LintroConfig | None = None,
) -> list[str]:
    """Build combined CLI arguments for config injection.

    Combines enforce CLI args and defaults config args.

    Args:
        tool_name: Name of the tool.
        lintro_config: Optional Lintro config. If None, loads current config.

    Returns:
        list[str]: Combined CLI arguments for config injection.
    """
    if lintro_config is None:
        lintro_config = _get_lintro_config()

    # Check if Lintro config should be used
    if not _should_use_lintro_config(tool_name, lintro_config):
        return []

    args: list[str] = []

    # Add enforce CLI args
    args.extend(_get_enforce_cli_args(tool_name=tool_name, lintro_config=lintro_config))

    # Add defaults config args
    args.extend(
        _get_defaults_config_args(tool_name=tool_name, lintro_config=lintro_config),
    )

    return args

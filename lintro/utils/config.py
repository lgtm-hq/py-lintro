"""Project configuration helpers for Lintro.

This module provides centralized access to configuration from pyproject.toml
and other config sources. It consolidates functionality from config_loaders
and config_utils into a single module.

Reads configuration from `pyproject.toml` under the `[tool.lintro]` table.
Allows tool-specific defaults via `[tool.lintro.<tool>]` (e.g., `[tool.lintro.ruff]`).
"""

from __future__ import annotations

import configparser
import tomllib
from pathlib import Path
from typing import Any

from loguru import logger

__all__ = [
    # Core pyproject loading
    "load_pyproject",
    "load_pyproject_config",
    "load_tool_config_from_pyproject",
    "clear_pyproject_cache",
    # Lintro config loading
    "load_lintro_global_config",
    "load_lintro_tool_config",
    "get_tool_order_config",
    "load_post_checks_config",
    # Tool-specific loaders
    "load_ruff_config",
    "load_bandit_config",
    "load_black_config",
    "load_mypy_config",
    "load_pydoclint_config",
    # Backward compatibility
    "get_central_line_length",
    "validate_line_length_consistency",
]


# =============================================================================
# Core pyproject.toml Loading
# =============================================================================

# Module-level caches keyed on resolved paths so that different working
# directories get independent cache entries (unlike functools.lru_cache
# which has no awareness of cwd).
_pyproject_path_cache: dict[Path, Path | None] = {}
_pyproject_data_cache: dict[Path, dict[str, Any]] = {}


def clear_pyproject_cache() -> None:
    """Clear both pyproject path and data caches.

    Call this when the working directory may have changed or when
    pyproject.toml contents should be re-read from disk.
    """
    _pyproject_path_cache.clear()
    _pyproject_data_cache.clear()


def _find_pyproject(start_path: Path | None = None) -> Path | None:
    """Search for pyproject.toml up the directory tree.

    Results are cached by resolved start path so that different working
    directories return the correct pyproject.toml.

    Args:
        start_path: Optional starting path for search.
                    Defaults to current working directory.

    Returns:
        Path to pyproject.toml if found, None otherwise.
    """
    if start_path is None:
        start_path = Path.cwd()
    key = start_path.resolve()
    if key in _pyproject_path_cache:
        return _pyproject_path_cache[key]
    for parent in [start_path, *start_path.parents]:
        candidate = parent / "pyproject.toml"
        if candidate.exists():
            _pyproject_path_cache[key] = candidate
            return candidate
    _pyproject_path_cache[key] = None
    return None


def load_pyproject() -> dict[str, Any]:
    """Load the full pyproject.toml with caching.

    Results are cached by resolved pyproject path so that different
    working directories correctly load their own pyproject.toml.

    Returns:
        Full pyproject.toml contents as dict
    """
    pyproject_path = _find_pyproject()
    if not pyproject_path:
        logger.debug("No pyproject.toml found in current directory or parents")
        return {}
    key = pyproject_path.resolve()
    if key in _pyproject_data_cache:
        return _pyproject_data_cache[key]
    try:
        with pyproject_path.open("rb") as f:
            data = tomllib.load(f)
        _pyproject_data_cache[key] = data
        return data
    except OSError as e:
        logger.warning(f"Failed to read pyproject.toml at {pyproject_path}: {e}")
        return {}
    except tomllib.TOMLDecodeError as e:
        logger.warning(f"Failed to parse pyproject.toml at {pyproject_path}: {e}")
        return {}


def load_pyproject_config() -> dict[str, Any]:
    """Load the entire pyproject.toml configuration.

    Alias for load_pyproject() for backward compatibility.

    Returns:
        dict[str, Any]: Complete pyproject.toml configuration, or empty dict if
        not found.
    """
    return load_pyproject()


def _get_lintro_section() -> dict[str, Any]:
    """Extract the [tool.lintro] section from pyproject.toml.

    Returns:
        The tool.lintro section as a dict, or {} if not found or invalid.
    """
    pyproject = load_pyproject()
    tool_section_raw = pyproject.get("tool", {})
    tool_section = tool_section_raw if isinstance(tool_section_raw, dict) else {}
    lintro_config_raw = tool_section.get("lintro", {})
    return lintro_config_raw if isinstance(lintro_config_raw, dict) else {}


# =============================================================================
# Lintro Configuration Loading
# =============================================================================


def load_lintro_global_config() -> dict[str, Any]:
    """Load global Lintro configuration from [tool.lintro].

    Returns:
        Global configuration dictionary (excludes tool-specific sections)
    """
    lintro_config = _get_lintro_section()

    # Filter out known tool-specific sections
    tool_sections = {
        "ruff",
        "black",
        "yamllint",
        "markdownlint",
        "markdownlint-cli2",
        "bandit",
        "hadolint",
        "actionlint",
        "pytest",
        "mypy",
        "clippy",
        "pydoclint",
        "tsc",
        "post_checks",
        "versions",
    }

    return {k: v for k, v in lintro_config.items() if k not in tool_sections}


def load_lintro_tool_config(tool_name: str) -> dict[str, Any]:
    """Load tool-specific Lintro config from [tool.lintro.<tool>].

    Args:
        tool_name: Name of the tool

    Returns:
        Tool-specific Lintro configuration
    """
    lintro_config = _get_lintro_section()
    tool_config = lintro_config.get(tool_name, {})
    return tool_config if isinstance(tool_config, dict) else {}


def get_tool_order_config() -> dict[str, Any]:
    """Get tool ordering configuration from [tool.lintro].

    Returns:
        Tool ordering configuration with keys:
        - strategy: "priority", "alphabetical", or "custom"
        - custom_order: list of tool names (for custom strategy)
        - priority_overrides: dict of tool -> priority (for priority strategy)
    """
    global_config = load_lintro_global_config()

    return {
        "strategy": global_config.get("tool_order", "priority"),
        "custom_order": global_config.get("tool_order_custom", []),
        "priority_overrides": global_config.get("tool_priorities", {}),
    }


def load_post_checks_config() -> dict[str, Any]:
    """Load post-checks configuration from pyproject.

    Returns:
        Dict with keys like:
            - enabled: bool
            - tools: list[str]
            - enforce_failure: bool
    """
    cfg = _get_lintro_section()
    section = cfg.get("post_checks", {})
    if isinstance(section, dict):
        return section
    return {}


# =============================================================================
# Tool Configuration Loading (from pyproject.toml [tool.<tool>])
# =============================================================================


def load_tool_config_from_pyproject(tool_name: str) -> dict[str, Any]:
    """Load tool-specific configuration from pyproject.toml [tool.<tool_name>].

    Args:
        tool_name: Name of the tool to load config for.

    Returns:
        dict[str, Any]: Tool configuration dictionary, or empty dict if not found.
    """
    pyproject_data = load_pyproject()
    tool_section = pyproject_data.get("tool", {})

    if tool_name in tool_section:
        config = tool_section[tool_name]
        if isinstance(config, dict):
            return config

    return {}


def load_ruff_config() -> dict[str, Any]:
    """Load ruff configuration from pyproject.toml with flattened lint settings.

    Returns:
        dict[str, Any]: Ruff configuration dictionary with flattened lint settings.
    """
    config = load_tool_config_from_pyproject("ruff")

    # Flatten nested lint section to top level for easy access
    if "lint" in config:
        lint_config = config["lint"]
        if isinstance(lint_config, dict):
            if "select" in lint_config:
                config["select"] = lint_config["select"]
            if "ignore" in lint_config:
                config["ignore"] = lint_config["ignore"]
            if "extend-select" in lint_config:
                config["extend_select"] = lint_config["extend-select"]
            if "extend-ignore" in lint_config:
                config["extend_ignore"] = lint_config["extend-ignore"]

    return config


def load_bandit_config() -> dict[str, Any]:
    """Load bandit configuration from pyproject.toml.

    Returns:
        dict[str, Any]: Bandit configuration dictionary.
    """
    return load_tool_config_from_pyproject("bandit")


def load_pydoclint_config() -> dict[str, Any]:
    """Load pydoclint configuration from pyproject.toml.

    Returns:
        dict[str, Any]: Pydoclint configuration dictionary.
    """
    return load_tool_config_from_pyproject("pydoclint")


def load_black_config() -> dict[str, Any]:
    """Load black configuration from pyproject.toml.

    Returns:
        dict[str, Any]: Black configuration dictionary.
    """
    return load_tool_config_from_pyproject("black")


def load_mypy_config(
    base_dir: Path | None = None,
) -> tuple[dict[str, Any], Path | None]:
    """Load mypy configuration from pyproject.toml or mypy.ini files.

    Args:
        base_dir: Directory to search for mypy configuration files.
            Defaults to the current working directory.

    Returns:
        tuple[dict[str, Any], Path | None]: Parsed configuration data and the
            path to the config file if found.
    """
    root = base_dir or Path.cwd()

    # Try pyproject.toml first
    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        try:
            with pyproject.open("rb") as handle:
                data = tomllib.load(handle)
            pyproject_config = data.get("tool", {}).get("mypy", {}) or {}
            if pyproject_config:
                return pyproject_config, pyproject
        except (OSError, tomllib.TOMLDecodeError, KeyError, TypeError) as e:
            logger.warning(f"Failed to load mypy config from pyproject.toml: {e}")

    # Fallback to mypy.ini or .mypy.ini
    for config_file in ["mypy.ini", ".mypy.ini"]:
        config_path = root / config_file
        if config_path.exists():
            try:
                parser = configparser.ConfigParser()
                parser.read(config_path)
                if "mypy" in parser:
                    config_dict = dict(parser["mypy"])
                    return config_dict, config_path
            except (OSError, configparser.Error) as e:
                logger.warning(f"Failed to load mypy config from {config_file}: {e}")

    return {}, None


# =============================================================================
# Backward Compatibility Functions
# =============================================================================


def get_central_line_length() -> int | None:
    """Get the central line length configuration.

    Backward-compatible wrapper that returns the effective line length
    for Ruff (which serves as the source of truth).

    Returns:
        Line length value if configured, None otherwise.
    """
    # Import here to avoid circular import
    from lintro.utils.unified_config import get_effective_line_length

    return get_effective_line_length("ruff")


def validate_line_length_consistency() -> list[str]:
    """Validate line length consistency across tools.

    Returns:
        List of warning messages about inconsistencies.
    """
    # Import here to avoid circular import
    from lintro.utils.unified_config import validate_config_consistency

    return validate_config_consistency()

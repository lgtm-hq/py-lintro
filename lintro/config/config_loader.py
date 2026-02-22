"""Configuration loader for Lintro.

Loads configuration from .lintro-config.yaml with fallback to
[tool.lintro] in pyproject.toml for backward compatibility.

Supports the new tiered configuration model:
1. execution: What tools run and how
2. enforce: Cross-cutting settings (replaces 'global')
3. defaults: Fallback config when no native config exists
4. tools: Per-tool enable/disable and config source
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from loguru import logger

from lintro.ai.config import AIConfig
from lintro.config.lintro_config import (
    EnforceConfig,
    ExecutionConfig,
    LintroConfig,
    LintroToolConfig,
)
from lintro.enums.config_key import ConfigKey

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

# Default config file name
LINTRO_CONFIG_FILENAME = ".lintro-config.yaml"
LINTRO_CONFIG_FILENAMES = [
    ".lintro-config.yaml",
    ".lintro-config.yml",
    "lintro-config.yaml",
    "lintro-config.yml",
]


def _find_config_file(start_dir: Path | None = None) -> Path | None:
    """Find .lintro-config.yaml by searching upward from start_dir.

    Args:
        start_dir: Directory to start searching from. Defaults to cwd.

    Returns:
        Path | None: Path to config file if found.
    """
    current = Path(start_dir) if start_dir else Path.cwd()
    current = current.resolve()

    while True:
        for filename in LINTRO_CONFIG_FILENAMES:
            config_path = current / filename
            if config_path.exists():
                return config_path

        # Move up one directory
        parent = current.parent
        if parent == current:
            # Reached filesystem root
            break
        current = parent

    return None


def _load_yaml_file(path: Path) -> dict[str, Any]:
    """Load a YAML file.

    Args:
        path: Path to YAML file.

    Returns:
        dict[str, Any]: Parsed YAML content.

    Raises:
        ImportError: If PyYAML is not installed.
    """
    if yaml is None:
        raise ImportError(
            "PyYAML is required to load .lintro-config.yaml. "
            "Install it with: pip install pyyaml",
        )

    with path.open(encoding="utf-8") as f:
        content = yaml.safe_load(f)

    return content if isinstance(content, dict) else {}


def _load_pyproject_fallback() -> tuple[dict[str, Any], Path | None]:
    """Load [tool.lintro] from pyproject.toml as fallback.

    Searches upward from current directory for pyproject.toml, consistent
    with _find_config_file's search behavior.

    Returns:
        tuple[dict[str, Any], Path | None]: Tuple of (config data, path to
            pyproject.toml). Path is None if no pyproject.toml was found.
    """
    current = Path.cwd().resolve()

    while True:
        pyproject_path = current / "pyproject.toml"
        if pyproject_path.exists():
            try:
                with pyproject_path.open("rb") as f:
                    data = tomllib.load(f)
                return data.get("tool", {}).get("lintro", {}), pyproject_path
            except tomllib.TOMLDecodeError as e:
                logger.warning(
                    f"Failed to parse pyproject.toml at {pyproject_path}: {e}",
                )
                return {}, None
            except OSError as e:
                logger.debug(f"Could not read pyproject.toml at {pyproject_path}: {e}")
                return {}, None

        # Move up one directory
        parent = current.parent
        if parent == current:
            # Reached filesystem root
            break
        current = parent

    return {}, None


def _parse_enforce_config(data: dict[str, Any]) -> EnforceConfig:
    """Parse enforce configuration section.

    Args:
        data: Raw 'enforce' or 'global' section from config.

    Returns:
        EnforceConfig: Parsed enforce configuration.
    """
    return EnforceConfig(
        line_length=data.get("line_length"),
        target_python=data.get("target_python"),
    )


def _parse_execution_config(data: dict[str, Any]) -> ExecutionConfig:
    """Parse execution configuration section.

    Args:
        data: Raw 'execution' section from config.

    Returns:
        ExecutionConfig: Parsed execution configuration.
    """
    enabled_tools = data.get("enabled_tools", [])
    if isinstance(enabled_tools, str):
        enabled_tools = [enabled_tools]

    tool_order = data.get("tool_order", "priority")

    return ExecutionConfig(
        enabled_tools=enabled_tools,
        tool_order=tool_order,
        fail_fast=data.get("fail_fast", False),
        parallel=data.get("parallel", True),
        auto_install_deps=data.get("auto_install_deps"),
    )


def _parse_tool_config(data: dict[str, Any]) -> LintroToolConfig:
    """Parse a single tool configuration.

    In the tiered model, tools only have enabled and optional config_source.

    Args:
        data: Raw tool configuration dict.

    Returns:
        LintroToolConfig: Parsed tool configuration.

    Raises:
        ValueError: If auto_install is not a boolean.
    """
    enabled = data.get("enabled", True)
    config_source = data.get("config_source")
    auto_install_raw = data.get("auto_install")
    auto_install: bool | None = None
    if isinstance(auto_install_raw, bool):
        auto_install = auto_install_raw
    elif auto_install_raw is not None:
        type_name = type(auto_install_raw).__name__
        raise ValueError(
            f"tools.<name>.auto_install must be a boolean, got {type_name}",
        )

    return LintroToolConfig(
        enabled=enabled,
        config_source=config_source,
        auto_install=auto_install,
    )


def _parse_tools_config(data: dict[str, Any]) -> dict[str, LintroToolConfig]:
    """Parse all tool configurations.

    Args:
        data: Raw 'tools' section from config.

    Returns:
        dict[str, LintroToolConfig]: Tool configurations keyed by tool name.
    """
    tools: dict[str, LintroToolConfig] = {}

    for tool_name, tool_data in data.items():
        if isinstance(tool_data, dict):
            tools[tool_name.lower()] = _parse_tool_config(tool_data)
        elif isinstance(tool_data, bool):
            # Simple enabled/disabled flag
            tools[tool_name.lower()] = LintroToolConfig(enabled=tool_data)

    return tools


def _parse_defaults(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Parse defaults configuration section.

    Args:
        data: Raw 'defaults' section from config.

    Returns:
        dict[str, dict[str, Any]]: Defaults configurations keyed by tool name.
    """
    defaults: dict[str, dict[str, Any]] = {}

    for tool_name, tool_defaults in data.items():
        if isinstance(tool_defaults, dict):
            defaults[tool_name.lower()] = tool_defaults

    return defaults


def _parse_ai_config(data: dict[str, Any]) -> AIConfig:
    """Parse AI configuration section.

    Passes only recognized keys through to AIConfig so the model's
    own defaults apply for any omitted fields.

    Args:
        data: Raw 'ai' section from config.

    Returns:
        AIConfig: Parsed AI configuration.
    """
    if not data:
        return AIConfig()

    known_fields = set(AIConfig.model_fields)
    filtered = {k: v for k, v in data.items() if k in known_fields}
    return AIConfig(**filtered)


def _convert_pyproject_to_config(data: dict[str, Any]) -> dict[str, Any]:
    """Convert pyproject.toml [tool.lintro] format to .lintro-config.yaml format.

    The pyproject format uses flat tool sections like [tool.lintro.ruff],
    while .lintro-config.yaml uses nested tools: section.

    Args:
        data: Raw [tool.lintro] section from pyproject.toml.

    Returns:
        dict[str, Any]: Converted configuration in .lintro-config.yaml format.
    """
    result: dict[str, Any] = {
        "enforce": {},
        "execution": {},
        "defaults": {},
        "tools": {},
        "ai": {},
    }

    # Known tool names to separate from enforce settings
    # Hardcoded list of supported tools for reliable config parsing
    # (ToolRegistry may not be populated yet during config loading)
    known_tools = {
        "actionlint",
        "bandit",
        "black",
        "clippy",
        "hadolint",
        "markdownlint",
        "mypy",
        "pytest",
        "ruff",
        "yamllint",
    }
    # Add common aliases for tools
    tool_aliases = {"markdownlint-cli2": "markdownlint"}
    known_tools.update(tool_aliases.keys())

    # Known execution settings
    execution_keys = {
        "enabled_tools",
        "tool_order",
        "fail_fast",
        "parallel",
        "auto_install_deps",
    }

    # Known enforce settings (formerly global)
    enforce_keys = {"line_length", "target_python"}

    for key, value in data.items():
        key_lower = key.lower()

        if key_lower in known_tools:
            # Tool-specific config - normalize aliases to canonical names
            canonical_name = tool_aliases.get(key_lower, key_lower)
            result["tools"][canonical_name] = value
        elif key in execution_keys or key.replace("-", "_") in execution_keys:
            # Execution config
            result["execution"][key.replace("-", "_")] = value
        elif key in enforce_keys or key.replace("-", "_") in enforce_keys:
            # Enforce config
            result["enforce"][key.replace("-", "_")] = value
        elif key_lower == ConfigKey.POST_CHECKS.value.lower():
            # Skip post_checks (handled separately)
            pass
        elif key_lower == ConfigKey.VERSIONS.value.lower():
            # Skip versions (handled separately)
            pass
        elif key_lower == ConfigKey.DEFAULTS.value.lower() and isinstance(value, dict):
            # Defaults section
            result["defaults"] = value
        elif key_lower == "ai" and isinstance(value, dict):
            # AI configuration section
            result["ai"] = value

    return result


def load_config(
    config_path: Path | str | None = None,
    allow_pyproject_fallback: bool = True,
) -> LintroConfig:
    """Load Lintro configuration.

    Priority:
    1. Explicit config_path if provided
    2. .lintro-config.yaml found by searching upward
    3. [tool.lintro] in pyproject.toml fallback
    4. Default empty configuration

    Args:
        config_path: Explicit path to config file. If None, searches for
            .lintro-config.yaml.
        allow_pyproject_fallback: Whether to fall back to pyproject.toml
            if no .lintro-config.yaml is found.

    Returns:
        LintroConfig: Loaded configuration.
    """
    data: dict[str, Any] = {}
    resolved_path: str | None = None

    # Try explicit path first
    if config_path:
        path = Path(config_path)
        if path.exists():
            data = _load_yaml_file(path)
            resolved_path = str(path.resolve())
            logger.debug(f"Loaded config from explicit path: {resolved_path}")
        else:
            logger.warning(f"Config file not found: {config_path}")

    # Try searching for .lintro-config.yaml
    if not data:
        found_path = _find_config_file()
        if found_path:
            data = _load_yaml_file(found_path)
            resolved_path = str(found_path.resolve())
            logger.debug(f"Loaded config from: {resolved_path}")

    # Fall back to pyproject.toml
    if not data and allow_pyproject_fallback:
        pyproject_data, pyproject_path = _load_pyproject_fallback()
        if pyproject_data:
            data = _convert_pyproject_to_config(pyproject_data)
            resolved_path = str(pyproject_path.resolve()) if pyproject_path else None
            logger.debug(
                "Using [tool.lintro] from pyproject.toml. "
                "Consider migrating to .lintro-config.yaml",
            )

    # Parse enforce config
    enforce_data = data.get("enforce", {})

    enforce_config = _parse_enforce_config(enforce_data)
    execution_config = _parse_execution_config(data.get("execution", {}))
    defaults = _parse_defaults(data.get("defaults", {}))
    tools_config = _parse_tools_config(data.get("tools", {}))
    ai_config = _parse_ai_config(data.get("ai", {}))

    return LintroConfig(
        execution=execution_config,
        enforce=enforce_config,
        defaults=defaults,
        tools=tools_config,
        ai=ai_config,
        config_path=resolved_path,
    )


def get_default_config() -> LintroConfig:
    """Get a default configuration with sensible defaults.

    Returns:
        LintroConfig: Default configuration.
    """
    return LintroConfig(
        enforce=EnforceConfig(
            line_length=88,
            target_python=None,
        ),
        execution=ExecutionConfig(
            tool_order="priority",
        ),
    )


# Global singleton for loaded config
_loaded_config: LintroConfig | None = None


def get_config(reload: bool = False) -> LintroConfig:
    """Get the loaded configuration singleton.

    Args:
        reload: Force reload from disk.

    Returns:
        LintroConfig: Loaded configuration.
    """
    global _loaded_config

    if _loaded_config is None or reload:
        _loaded_config = load_config()

    return _loaded_config


def clear_config_cache() -> None:
    """Clear the configuration cache.

    Useful for testing or when config file has changed.
    """
    global _loaded_config
    _loaded_config = None

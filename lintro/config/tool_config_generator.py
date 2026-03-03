"""Tool configuration generator for Lintro.

This module provides CLI argument injection for enforced settings and
default config generation for tools without native configs.

The tiered configuration model:
1. EXECUTION: What tools run and how
2. ENFORCE: Cross-cutting settings injected via CLI flags
3. DEFAULTS: Fallback config when no native config exists
4. TOOLS: Per-tool enable/disable and config source
"""

from __future__ import annotations

import atexit
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from loguru import logger

from lintro.config.lintro_config import LintroConfig
from lintro.enums.config_format import ConfigFormat
from lintro.enums.tool_name import ToolName

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]


# CLI flags for enforced settings: setting -> {tool: flag}
ENFORCE_CLI_FLAGS: dict[str, dict[str, str]] = {
    "line_length": {
        "ruff": "--line-length",
        "black": "--line-length",
    },
    "target_python": {
        "ruff": "--target-version",
        "black": "--target-version",
        "mypy": "--python-version",
    },
}


def _convert_python_version_for_mypy(version: str) -> str:
    """Convert ``py313`` style strings to ``3.13`` for mypy.

    Args:
        version: Python version string, often ``py313`` format.

    Returns:
        str: Version string formatted for mypy (for example, ``3.13``).
    """
    if version.startswith("py") and len(version) >= 4:
        major = version[2]
        minor = version[3:]
        return f"{major}.{minor}"
    return version


# Tool config format for defaults generation
TOOL_CONFIG_FORMATS: dict[str, ConfigFormat] = {
    "bandit": ConfigFormat.YAML,
    "hadolint": ConfigFormat.YAML,
    "markdownlint": ConfigFormat.JSON,
    "oxfmt": ConfigFormat.JSON,
    "oxlint": ConfigFormat.JSON,
    "yamllint": ConfigFormat.YAML,
    "prettier": ConfigFormat.JSON,
}

# Key mappings for tools that use different naming conventions in their native configs.
# Maps lintro config keys (snake_case) to native tool keys (often camelCase).
NATIVE_KEY_MAPPINGS: dict[str, dict[str, str]] = {
    "hadolint": {
        "trusted_registries": "trustedRegistries",
        "require_labels": "requireLabels",
        "strict_labels": "strictLabels",
        # "ignored" stays as "ignored" (same in both formats)
    },
}

# Built-in defaults that Lintro provides for tools even without user config.
# User defaults always take precedence (merged on top of builtins).
TOOL_BUILTIN_DEFAULTS: dict[str, dict[str, Any]] = {
    "prettier": {
        "proseWrap": "always",
    },
}

# Native config file patterns for checking if tool has native config
NATIVE_CONFIG_PATTERNS: dict[str, list[str]] = {
    "markdownlint": [
        ".markdownlint-cli2.jsonc",
        ".markdownlint-cli2.yaml",
        ".markdownlint-cli2.cjs",
        ".markdownlint.jsonc",
        ".markdownlint.json",
        ".markdownlint.yaml",
        ".markdownlint.yml",
        ".markdownlint.cjs",
    ],
    "yamllint": [
        ".yamllint",
        ".yamllint.yaml",
        ".yamllint.yml",
    ],
    "hadolint": [
        ".hadolint.yaml",
        ".hadolint.yml",
    ],
    "bandit": [
        ".bandit",
        ".bandit.yaml",
        ".bandit.yml",
        "bandit.yaml",
        "bandit.yml",
    ],
    "oxlint": [
        ".oxlintrc.json",
    ],
    "oxfmt": [
        ".oxfmtrc.json",
        ".oxfmtrc.jsonc",
    ],
    "prettier": [
        ".prettierrc",
        ".prettierrc.json",
        ".prettierrc.json5",
        ".prettierrc.yaml",
        ".prettierrc.yml",
        ".prettierrc.js",
        ".prettierrc.cjs",
        ".prettierrc.mjs",
        ".prettierrc.toml",
        "prettier.config.js",
        "prettier.config.cjs",
        "prettier.config.mjs",
        "prettier.config.ts",
        "prettier.config.cts",
        "prettier.config.mts",
    ],
}

# Track temporary files for cleanup
_temp_files: list[Path] = []


def _cleanup_temp_files() -> None:
    """Clean up temporary config files on exit."""
    for temp_file in _temp_files:
        try:
            if temp_file.exists():
                temp_file.unlink()
                logger.debug(f"Cleaned up temp config: {temp_file}")
        except OSError as e:
            logger.debug(f"Failed to clean up {temp_file}: {e}")


# Register cleanup on exit
atexit.register(_cleanup_temp_files)


def get_enforce_cli_args(
    tool_name: str,
    lintro_config: LintroConfig,
) -> list[str]:
    """Get CLI arguments for enforced settings.

    These settings override native tool configs to ensure consistency
    across different tools for shared concerns like line length.

    Args:
        tool_name: Name of the tool (e.g., "ruff", "black").
        lintro_config: Lintro configuration.

    Returns:
        list[str]: CLI arguments to inject (e.g., ["--line-length", "88"]).
    """
    args: list[str] = []
    tool_lower = tool_name.lower()
    enforce = lintro_config.enforce

    # Inject line_length if set
    if enforce.line_length is not None:
        flag = ENFORCE_CLI_FLAGS.get("line_length", {}).get(tool_lower)
        if flag:
            args.extend([flag, str(enforce.line_length)])
            logger.debug(
                f"Injecting enforce.line_length={enforce.line_length} "
                f"to {tool_name} as {flag}",
            )

    # Inject target_python if set
    if enforce.target_python is not None:
        flag = ENFORCE_CLI_FLAGS.get("target_python", {}).get(tool_lower)
        if flag:
            target_value = (
                _convert_python_version_for_mypy(enforce.target_python)
                if tool_lower == ToolName.MYPY.value
                else enforce.target_python
            )
            args.extend([flag, target_value])
            logger.debug(
                f"Injecting enforce.target_python={target_value} "
                f"to {tool_name} as {flag}",
            )

    return args


def has_native_config(tool_name: str) -> bool:
    """Check if a tool has a native config file in the project.

    Searches for known native config file patterns starting from the
    current working directory and moving upward to find the project root.

    Args:
        tool_name: Name of the tool (e.g., "markdownlint").

    Returns:
        bool: True if a native config file exists.
    """
    tool_lower = tool_name.lower()
    patterns = NATIVE_CONFIG_PATTERNS.get(tool_lower, [])

    if not patterns:
        return False

    # Search from current directory upward
    current = Path.cwd().resolve()

    while True:
        for pattern in patterns:
            config_path = current / pattern
            if config_path.exists():
                logger.debug(
                    f"Found native config for {tool_name}: {config_path}",
                )
                return True

        # Move up one directory
        parent = current.parent
        if parent == current:
            # Reached filesystem root
            break
        current = parent

    return False


def generate_defaults_config(
    tool_name: str,
    lintro_config: LintroConfig,
) -> Path | None:
    """Generate a temporary config file from defaults.

    Only used when a tool has no native config file and defaults
    are specified in the Lintro config.

    Args:
        tool_name: Name of the tool.
        lintro_config: Lintro configuration.

    Returns:
        Path | None: Path to generated config file, or None if not needed.
    """
    tool_lower = tool_name.lower()

    # Check if tool has native config - if so, don't generate defaults
    if has_native_config(tool_lower):
        logger.debug(
            f"Tool {tool_name} has native config, skipping defaults generation",
        )
        return None

    # Get defaults for this tool (user defaults override builtin defaults)
    user_defaults = lintro_config.get_tool_defaults(tool_lower)
    builtin_defaults = TOOL_BUILTIN_DEFAULTS.get(tool_lower, {})
    if not user_defaults and not builtin_defaults:
        return None
    defaults = {**builtin_defaults, **user_defaults}

    # Get config format for this tool
    config_format = TOOL_CONFIG_FORMATS.get(tool_lower, ConfigFormat.JSON)

    try:
        return _write_defaults_config(
            defaults=defaults,
            tool_name=tool_lower,
            config_format=config_format,
        )
    except (OSError, ValueError, TypeError, ImportError) as e:
        logger.error(
            f"Failed to generate defaults config for {tool_name}: "
            f"{type(e).__name__}: {e}",
        )
        return None


def _transform_keys_for_native_config(
    defaults: dict[str, Any],
    tool_name: str,
) -> dict[str, Any]:
    """Transform lintro config keys to native tool key format.

    Some tools (like hadolint) use camelCase keys in their native config files,
    while lintro uses snake_case for consistency. This function transforms keys
    to match the native tool's expected format.

    Args:
        defaults: Default configuration dictionary with lintro keys.
        tool_name: Name of the tool.

    Returns:
        dict[str, Any]: Configuration with keys transformed to native format.
    """
    key_mapping = NATIVE_KEY_MAPPINGS.get(tool_name.lower(), {})
    if not key_mapping:
        return defaults

    transformed: dict[str, Any] = {}
    for key, value in defaults.items():
        native_key = key_mapping.get(key, key)
        transformed[native_key] = value

    if transformed != defaults:
        logger.debug(
            f"Transformed config keys for {tool_name}: "
            f"{list(defaults.keys())} -> {list(transformed.keys())}",
        )

    return transformed


def _write_defaults_config(
    defaults: dict[str, Any],
    tool_name: str,
    config_format: ConfigFormat,
) -> Path:
    """Write defaults configuration to a temporary file.

    Args:
        defaults: Default configuration dictionary.
        tool_name: Name of the tool.
        config_format: Output format (json, yaml).

    Returns:
        Path: Path to temporary config file.

    Raises:
        ImportError: If PyYAML is not installed and YAML format is requested.
    """
    # Tool-specific suffixes required by some tools (e.g., markdownlint-cli2 v0.17+
    # enforces strict config file naming conventions)
    tool_suffix_overrides: dict[str, str] = {
        "markdownlint": ".markdownlint-cli2.jsonc",
    }

    tool_lower = tool_name.lower()
    if tool_lower in tool_suffix_overrides:
        suffix = tool_suffix_overrides[tool_lower]
    else:
        suffix_map = {ConfigFormat.JSON: ".json", ConfigFormat.YAML: ".yaml"}
        suffix = suffix_map.get(config_format, ".json")

    temp_fd, temp_path_str = tempfile.mkstemp(
        prefix=f"lintro-{tool_name}-defaults-",
        suffix=suffix,
    )
    os.close(temp_fd)
    temp_path = Path(temp_path_str)
    _temp_files.append(temp_path)

    # Transform keys to native format before writing
    native_defaults = _transform_keys_for_native_config(defaults, tool_lower)

    if config_format == ConfigFormat.YAML:
        if yaml is None:
            raise ImportError("PyYAML required for YAML output")
        content = yaml.dump(native_defaults, default_flow_style=False)
    else:
        content = json.dumps(native_defaults, indent=2)

    temp_path.write_text(content, encoding="utf-8")
    logger.debug(f"Generated defaults config for {tool_name}: {temp_path}")

    return temp_path


def get_defaults_injection_args(
    tool_name: str,
    config_path: Path | None,
) -> list[str]:
    """Get CLI arguments to inject defaults config file into a tool.

    Args:
        tool_name: Name of the tool.
        config_path: Path to defaults config file (or None).

    Returns:
        list[str]: CLI arguments to pass to the tool.
    """
    if config_path is None:
        return []

    tool_lower = tool_name.lower()
    config_str = str(config_path)

    # Tool-specific config flags
    config_flags: dict[str, list[str]] = {
        "yamllint": ["-c", config_str],
        "markdownlint": ["--config", config_str],
        "hadolint": ["--config", config_str],
        "bandit": ["-c", config_str],
        "oxlint": ["--config", config_str],
        "oxfmt": ["--config", config_str],
        "prettier": ["--no-config", "--config", config_str],
    }

    return config_flags.get(tool_lower, [])


def cleanup_temp_config(config_path: Path) -> None:
    """Explicitly clean up a temporary config file.

    Args:
        config_path: Path to temporary config file.
    """
    try:
        if config_path in _temp_files:
            _temp_files.remove(config_path)
        if config_path.exists():
            config_path.unlink()
            logger.debug(f"Cleaned up temp config: {config_path}")
    except OSError as e:
        logger.debug(f"Failed to clean up {config_path}: {e}")


# =============================================================================

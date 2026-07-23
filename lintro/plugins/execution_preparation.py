"""Execution preparation utilities for tool plugins.

This module provides execution preparation, version checking, and config injection.
"""

from __future__ import annotations

import os
from typing import Any

from loguru import logger

from lintro.config.lintro_config import LintroConfig
from lintro.models.core.tool_result import ToolResult
from lintro.plugins.file_discovery import discover_files, get_cwd, validate_paths
from lintro.plugins.protocol import ToolDefinition

# Constants for default values
DEFAULT_TIMEOUT: int = 30

# Comma-separated tool names (or ``*``) whose below-minimum binaries may still
# run. Mirrors CI ``--allow-version-lag`` (#1582): digest-pinned tools images
# lag a Renovate manifest bump until the post-merge republish lands. Without
# this, plugins skip the lagging binary and integration checks that expect
# findings (e.g. trufflehog secrets) fail with ``issues_count == 0``.
_ALLOW_VERSION_LAG_ENV: str = "LINTRO_ALLOW_VERSION_LAG"


def _parse_allow_version_lag(raw: str | None) -> set[str] | None:
    """Parse ``LINTRO_ALLOW_VERSION_LAG`` into a tool-name set.

    Args:
        raw: Env value: comma-separated tool names, ``*`` for all, or empty.

    Returns:
        A set of lowercased tool names, ``None`` when ``*`` (allow all), or an
        empty set when unset/blank (full enforcement).
    """
    if raw is None:
        return set()
    text = raw.strip()
    if not text:
        return set()
    if text == "*":
        return None
    names = {part.strip().lower() for part in text.split(",") if part.strip()}
    return names


def _version_lag_allowed(tool_name: str) -> bool:
    """Return whether ``tool_name`` may run when below the minimum version.

    Args:
        tool_name: Tool name from the tool definition.

    Returns:
        True when the tool is listed in ``LINTRO_ALLOW_VERSION_LAG`` (or ``*``).
    """
    allow = _parse_allow_version_lag(os.environ.get(_ALLOW_VERSION_LAG_ENV))
    if allow is None:
        return True
    return tool_name.lower() in allow


def get_effective_timeout(
    timeout: int | float | None,
    options: dict[str, object],
    default_timeout: int,
) -> float:
    """Get the effective timeout value.

    Args:
        timeout: Override timeout value, or None to use default.
        options: Options dict that may contain timeout.
        default_timeout: Default timeout from definition.

    Returns:
        Timeout value in seconds.
    """
    if timeout is not None:
        return float(timeout)

    raw_timeout = options.get("timeout", default_timeout)
    if isinstance(raw_timeout, (int, float)):
        return float(raw_timeout)

    # Warn about invalid timeout value
    if raw_timeout is not None:
        type_name = type(raw_timeout).__name__
        logger.warning(
            f"Invalid timeout value {raw_timeout!r} (type {type_name}), "
            f"using default {default_timeout}s",
        )
    return float(default_timeout)


def get_executable_command(tool_name: str) -> list[str]:
    """Get the command prefix to execute a tool.

    Delegates to CommandBuilderRegistry for language-specific logic.

    Args:
        tool_name: Name of the tool executable.

    Returns:
        Command prefix list.
    """
    from lintro.enums.tool_name import normalize_tool_name
    from lintro.tools.core.command_builders import CommandBuilderRegistry

    try:
        tool_name_enum = normalize_tool_name(tool_name)
    except ValueError:
        tool_name_enum = None

    result: list[str] = CommandBuilderRegistry.get_command(tool_name, tool_name_enum)
    return result


def verify_tool_version(definition: ToolDefinition) -> ToolResult | None:
    """Verify that the tool meets minimum version requirements.

    When ``LINTRO_ALLOW_VERSION_LAG`` lists the tool (or is ``*``) and the
    binary is present but older than the manifest minimum, proceed with a
    warning instead of skipping. Missing binaries and other hard failures
    still skip. This matches the CI image gate's ``--allow-version-lag``
    policy for Renovate tool bumps against a digest-pinned base image.

    Args:
        definition: Tool definition with name.

    Returns:
        None if version check passes, or a skip result if it fails.
    """
    from lintro.tools.core.version_requirements import check_tool_version

    command = get_executable_command(definition.name)
    version_info = check_tool_version(definition.name, command)

    if version_info.version_check_passed:
        if version_info.below_recommended:
            logger.warning(
                "{} {} is below recommended version {} (minimum {} met)",
                definition.name,
                version_info.current_version,
                version_info.recommended_version,
                version_info.min_version,
            )
        return None

    # Binary exists and responded but version could not be parsed — proceed
    if (
        version_info.current_version is None
        and version_info.error_message
        and "Could not parse version" in version_info.error_message
    ):
        import shutil

        main_cmd = command[0] if command else definition.name
        if shutil.which(main_cmd):
            logger.debug(
                "Could not parse version for {}, proceeding anyway",
                definition.name,
            )
            return None

    # Digest-pinned image lag after a manifest version bump: binary exists and
    # is merely older than min_version. Allowlisted tools keep running so
    # integration coverage is not silently reduced to a skip.
    if (
        version_info.current_version is not None
        and version_info.error_message
        and "below minimum requirement" in version_info.error_message
        and _version_lag_allowed(definition.name)
    ):
        logger.warning(
            "{} {} is below minimum {} but allowed via {}; proceeding",
            definition.name,
            version_info.current_version,
            version_info.min_version,
            _ALLOW_VERSION_LAG_ENV,
        )
        return None

    skip_message = (
        f"Skipping {definition.name}: {version_info.error_message}. "
        f"Minimum required: {version_info.min_version}. "
        f"{version_info.install_hint}"
    )

    return ToolResult(
        name=definition.name,
        success=True,
        output=skip_message,
        issues_count=0,
        skipped=True,
        skip_reason=version_info.error_message,
    )


def prepare_execution(
    paths: list[str],
    options: dict[str, object],
    definition: ToolDefinition,
    exclude_patterns: list[str],
    include_venv: bool,
    current_options: dict[str, object],
    no_files_message: str = "No files to check.",
) -> dict[str, Any]:
    """Prepare execution context with common boilerplate steps.

    This function consolidates repeated patterns:
    - Merge options with defaults
    - Validate input paths
    - Discover files matching patterns (returns early if none found)
    - Verify tool version requirements (skipped when no files match)
    - Compute working directory and relative paths
    - Compute timeout for execution

    Args:
        paths: Input paths to process.
        options: Runtime options to merge with defaults.
        definition: Tool definition.
        exclude_patterns: Patterns to exclude.
        include_venv: Whether to include venv files.
        current_options: Current plugin options.
        no_files_message: Message when no files are found.

    Returns:
        Dictionary with files, rel_files, cwd, timeout, and optional early_result.
    """
    # Merge runtime options with defaults
    merged_options = dict(current_options)
    merged_options.update(options)

    # Validate paths
    validate_paths(paths)
    if not paths:
        return {
            "early_result": ToolResult(
                name=definition.name,
                success=True,
                output=no_files_message,
                issues_count=0,
            ),
        }

    # Discover files matching tool patterns, restricting to the git-diff
    # changed set when a base ref was resolved for this run.
    diff_base = merged_options.get("diff_base")
    files = discover_files(
        paths=paths,
        definition=definition,
        exclude_patterns=exclude_patterns,
        include_venv=include_venv,
        diff_base=diff_base if isinstance(diff_base, str) else None,
        incremental=bool(merged_options.get("incremental", False)),
    )

    if not files:
        file_type = "files"
        patterns = definition.file_patterns
        if patterns:
            extensions = [p.replace("*", "") for p in patterns if p.startswith("*.")]
            if extensions:
                file_type = "/".join(extensions) + " files"

        return {
            "early_result": ToolResult(
                name=definition.name,
                success=True,
                output=f"No {file_type} found to check.",
                issues_count=0,
            ),
        }

    # Check version requirements (only when files exist to check)
    version_result = verify_tool_version(definition)
    if version_result is not None:
        return {"early_result": version_result}

    logger.debug(f"Files to process: {files}")

    # Compute cwd and relative paths
    cwd = get_cwd(files)
    rel_files = [os.path.relpath(f, cwd) if cwd else f for f in files]

    # Get timeout (keep as float to preserve precision)
    timeout_value = merged_options.get("timeout")
    timeout = get_effective_timeout(
        timeout_value if isinstance(timeout_value, (int, float)) else None,
        merged_options,
        definition.default_timeout,
    )

    logger.debug(
        f"Prepared execution: {len(files)} files, cwd={cwd}, timeout={timeout}s",
    )
    return {
        "files": files,
        "rel_files": rel_files,
        "cwd": cwd,
        "timeout": timeout,
    }


# -------------------------------------------------------------------------
# Lintro Config Support Functions
# -------------------------------------------------------------------------


def get_lintro_config() -> LintroConfig:
    """Get the current Lintro configuration.

    Returns:
        The current LintroConfig instance.
    """
    from lintro.tools.core.config_injection import _get_lintro_config

    result: LintroConfig = _get_lintro_config()
    return result


def get_enforced_settings(
    lintro_config: LintroConfig | None = None,
) -> dict[str, object]:
    """Get enforced settings as a dictionary.

    Args:
        lintro_config: Optional config to use, or None to get current.

    Returns:
        Dictionary of enforced settings.
    """
    from lintro.tools.core.config_injection import _get_enforced_settings

    config = lintro_config or get_lintro_config()
    result: dict[str, object] = _get_enforced_settings(lintro_config=config)
    return result


def get_enforce_cli_args(
    tool_name: str,
    lintro_config: LintroConfig | None = None,
) -> list[str]:
    """Get CLI arguments for enforced settings.

    Args:
        tool_name: Name of the tool.
        lintro_config: Optional config to use, or None to get current.

    Returns:
        List of CLI arguments for enforced settings.
    """
    from lintro.tools.core.config_injection import _get_enforce_cli_args

    config = lintro_config or get_lintro_config()
    result: list[str] = _get_enforce_cli_args(tool_name=tool_name, lintro_config=config)
    return result


def get_defaults_config_args(
    tool_name: str,
    lintro_config: LintroConfig | None = None,
) -> list[str]:
    """Get CLI arguments for defaults config injection.

    Args:
        tool_name: Name of the tool.
        lintro_config: Optional config to use, or None to get current.

    Returns:
        List of CLI arguments for defaults config.
    """
    from lintro.tools.core.config_injection import _get_defaults_config_args

    config = lintro_config or get_lintro_config()
    result: list[str] = _get_defaults_config_args(
        tool_name=tool_name,
        lintro_config=config,
    )
    return result


def should_use_lintro_config(tool_name: str) -> bool:
    """Check if Lintro config should be used for this tool.

    Args:
        tool_name: Name of the tool.

    Returns:
        True if Lintro config should be used.
    """
    from lintro.tools.core.config_injection import _should_use_lintro_config

    result: bool = _should_use_lintro_config(tool_name=tool_name)
    return result


def build_config_args(
    tool_name: str,
    lintro_config: LintroConfig | None = None,
) -> list[str]:
    """Build combined CLI arguments for config injection.

    Args:
        tool_name: Name of the tool.
        lintro_config: Optional config to use, or None to get current.

    Returns:
        List of combined CLI arguments for config.
    """
    from lintro.tools.core.config_injection import _build_config_args

    config = lintro_config or get_lintro_config()
    result: list[str] = _build_config_args(tool_name=tool_name, lintro_config=config)
    return result

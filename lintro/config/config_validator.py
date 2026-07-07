"""Validation for Lintro configuration files.

Provides a schema-aware validator used by ``lintro config validate``. It
surfaces two classes of problems:

- ``errors``: the config cannot be loaded as-is (bad types, invalid values).
- ``warnings``: the config loads, but contains suspicious content such as
  unknown tools (often typos), unknown keys, or deprecated options.

The validator is intentionally decoupled from Click so it can be unit tested
directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import get_close_matches
from pathlib import Path
from typing import Any

from lintro.config.config_loader import (
    _convert_pyproject_to_config,
    _find_config_file,
    load_config,
)
from lintro.enums.tool_name import ToolName

try:
    import yaml
except ImportError:  # pragma: no cover - enforced by packaging
    yaml = None  # type: ignore[assignment]

# Recognized top-level sections in .lintro-config.yaml.
KNOWN_TOP_LEVEL_KEYS: frozenset[str] = frozenset(
    {"enforce", "execution", "defaults", "tools", "ai", "review"},
)

# Recognized keys within the ``execution`` section.
KNOWN_EXECUTION_KEYS: frozenset[str] = frozenset(
    {
        "enabled_tools",
        "tool_order",
        "fail_fast",
        "parallel",
        "auto_install_deps",
        "max_fix_retries",
    },
)

# Recognized keys within the ``enforce`` section.
KNOWN_ENFORCE_KEYS: frozenset[str] = frozenset({"line_length", "target_python"})

# Recognized keys within a per-tool ``tools.<name>`` mapping.
KNOWN_TOOL_KEYS: frozenset[str] = frozenset(
    {"enabled", "config_source", "auto_install"},
)

# Deprecated option names mapped to their modern replacement.
DEPRECATED_KEYS: dict[str, str] = {
    "line-length": "line_length",
    "target-python": "target_python",
    "global": "enforce",
}


def known_tool_names() -> frozenset[str]:
    """Return the set of recognized tool names.

    Includes both the canonical underscore form and the hyphenated form for
    each tool, plus common aliases, so validation matches the loader's
    tolerance.

    Returns:
        frozenset[str]: Recognized tool identifiers.
    """
    names: set[str] = set()
    for tool in ToolName:
        names.add(tool.value)
        names.add(tool.value.replace("_", "-"))
    names.add("markdownlint-cli2")
    return frozenset(names)


@dataclass
class ValidationMessage:
    """A single validation finding.

    Attributes:
        message: Human-readable description of the finding.
        location: Optional dotted path to the offending config key.
        suggestion: Optional corrected value (e.g. a ``did you mean`` hint).
    """

    message: str
    location: str | None = None
    suggestion: str | None = None

    def render(self) -> str:
        """Render the message as a single display string.

        Returns:
            str: Formatted message including location and suggestion.
        """
        parts: list[str] = []
        if self.location:
            parts.append(f"{self.location}: ")
        parts.append(self.message)
        if self.suggestion:
            parts.append(f" (did you mean '{self.suggestion}'?)")
        return "".join(parts)


@dataclass
class ValidationResult:
    """Outcome of validating a configuration file.

    Attributes:
        config_path: Path to the validated file, or None if none was found.
        errors: Findings that make the configuration invalid.
        warnings: Non-fatal findings worth surfacing.
    """

    config_path: Path | None
    errors: list[ValidationMessage] = field(default_factory=list)
    warnings: list[ValidationMessage] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        """Whether the configuration is free of errors.

        Returns:
            bool: True when there are no errors.
        """
        return not self.errors


def _suggest(name: str, candidates: frozenset[str]) -> str | None:
    """Return the closest known candidate for a possibly-misspelled name.

    Args:
        name: The provided (possibly invalid) name.
        candidates: Recognized names to match against.

    Returns:
        str | None: Closest match, or None if nothing is close enough.
    """
    matches = get_close_matches(name, sorted(candidates), n=1, cutoff=0.6)
    return matches[0] if matches else None


def _check_unknown_keys(
    data: dict[str, Any],
    known: frozenset[str],
    prefix: str,
    warnings: list[ValidationMessage],
) -> None:
    """Append warnings for unknown or deprecated keys in a mapping.

    Args:
        data: Mapping to inspect.
        known: Recognized keys for this mapping.
        prefix: Dotted path prefix for messages (e.g. ``execution``).
        warnings: List to append findings to.
    """
    for key in data:
        if key in known:
            continue
        location = f"{prefix}.{key}" if prefix else key
        if key in DEPRECATED_KEYS:
            warnings.append(
                ValidationMessage(
                    message="deprecated option",
                    location=location,
                    suggestion=DEPRECATED_KEYS[key],
                ),
            )
            continue
        warnings.append(
            ValidationMessage(
                message="unknown option",
                location=location,
                suggestion=_suggest(key, known),
            ),
        )


def _check_tool_names(
    data: dict[str, Any],
    warnings: list[ValidationMessage],
) -> None:
    """Warn about unknown tool names in the ``tools`` section.

    Args:
        data: The ``tools`` mapping.
        warnings: List to append findings to.
    """
    known = known_tool_names()
    for name, tool_data in data.items():
        if name.lower() in known:
            if isinstance(tool_data, dict):
                _check_unknown_keys(
                    tool_data,
                    KNOWN_TOOL_KEYS,
                    f"tools.{name}",
                    warnings,
                )
            continue
        warnings.append(
            ValidationMessage(
                message=f"unknown tool '{name}'",
                location="tools",
                suggestion=_suggest(name.lower(), known),
            ),
        )


def _check_enabled_tools(
    execution: dict[str, Any],
    warnings: list[ValidationMessage],
) -> None:
    """Warn about unknown tool names in ``execution.enabled_tools``.

    Args:
        execution: The ``execution`` mapping.
        warnings: List to append findings to.
    """
    enabled = execution.get("enabled_tools")
    if isinstance(enabled, str):
        enabled = [enabled]
    if not isinstance(enabled, list):
        return
    known = known_tool_names()
    for name in enabled:
        if not isinstance(name, str) or name.lower() in known:
            continue
        warnings.append(
            ValidationMessage(
                message=f"unknown tool '{name}'",
                location="execution.enabled_tools",
                suggestion=_suggest(name.lower(), known),
            ),
        )


def validate_config_file(path: Path | str | None = None) -> ValidationResult:
    """Validate a Lintro configuration file.

    Loads the config (or locates one by searching upward), checks it against
    the known schema, and reports both hard errors and softer warnings.

    Args:
        path: Explicit path to a config file. When None, the nearest
            ``.lintro-config.yaml`` is located by searching upward.

    Returns:
        ValidationResult: Structured validation outcome.
    """
    config_path: Path
    if path is not None:
        config_path = Path(path)
        if not config_path.exists():
            return ValidationResult(
                config_path=config_path,
                errors=[
                    ValidationMessage(message=f"Config file not found: {config_path}"),
                ],
            )
    else:
        found = _find_config_file()
        if found is None:
            return ValidationResult(
                config_path=None,
                errors=[
                    ValidationMessage(
                        message=(
                            "No .lintro-config.yaml found. "
                            "Run 'lintro init' to create one."
                        ),
                    ),
                ],
            )
        config_path = found

    result = ValidationResult(config_path=config_path)

    if yaml is None:  # pragma: no cover - enforced by packaging
        result.errors.append(
            ValidationMessage(
                message="PyYAML is required to validate configuration.",
            ),
        )
        return result

    try:
        raw = config_path.read_text(encoding="utf-8")
        parsed = yaml.safe_load(raw)
    except (OSError, yaml.YAMLError) as exc:
        result.errors.append(
            ValidationMessage(message=f"Could not parse config: {exc}"),
        )
        return result

    if parsed is None:
        result.warnings.append(
            ValidationMessage(message="Config file is empty."),
        )
        return result

    if not isinstance(parsed, dict):
        result.errors.append(
            ValidationMessage(
                message=(
                    f"Config root must be a mapping, got {type(parsed).__name__}."
                ),
            ),
        )
        return result

    # pyproject.toml uses a flat [tool.lintro] layout; normalize before
    # structural checks so nested keys line up with the schema.
    is_pyproject = config_path.name == "pyproject.toml"
    if is_pyproject:
        parsed = _convert_pyproject_to_config(
            parsed.get("tool", {}).get("lintro", {}),
        )
    else:
        _check_unknown_keys(parsed, KNOWN_TOP_LEVEL_KEYS, "", result.warnings)

    execution = parsed.get("execution")
    if isinstance(execution, dict):
        _check_unknown_keys(
            execution,
            KNOWN_EXECUTION_KEYS,
            "execution",
            result.warnings,
        )
        _check_enabled_tools(execution, result.warnings)

    enforce = parsed.get("enforce")
    if isinstance(enforce, dict):
        _check_unknown_keys(enforce, KNOWN_ENFORCE_KEYS, "enforce", result.warnings)

    tools = parsed.get("tools")
    if isinstance(tools, dict):
        _check_tool_names(tools, result.warnings)

    # Run the real loader to catch typed/value errors (max_fix_retries,
    # auto_install, review schema, etc.). pyproject configs are validated via
    # the standard search path rather than an explicit file path.
    try:
        load_config(config_path=None if is_pyproject else config_path)
    except ValueError as exc:
        result.errors.append(ValidationMessage(message=str(exc)))

    return result

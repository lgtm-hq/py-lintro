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

import tomllib
from dataclasses import dataclass, field
from difflib import get_close_matches
from pathlib import Path
from typing import Any

from lintro.config.config_loader import (
    EXTERNALLY_HANDLED_SECTIONS,
    _convert_pyproject_to_config,
    _find_config_file,
    _pyproject_lintro_catalog,
    build_config_from_dict,
    known_config_tool_names,
)
from lintro.config.lintro_config import (
    EnforceConfig,
    ExecutionConfig,
    LintroConfig,
    LintroToolConfig,
    OutputConfig,
    ReviewConfig,
    ScoreConfig,
)
from lintro.enums.validation_code import ValidationCode
from lintro.utils.config import STRUCTURAL_SECTIONS, _find_pyproject

try:
    import yaml
except ImportError:  # pragma: no cover - enforced by packaging
    yaml = None  # type: ignore[assignment]

# Recognized top-level sections in .lintro-config.yaml. Derived from the real
# schema rather than hand-copied: every ``LintroConfig`` field (minus the
# loader-populated ``config_path``) plus the sections that live in the config
# file but are parsed by other loaders. Hand-maintained lists drifted from the
# loader and warned about valid ``score:``/``output:``/``plugins:`` sections.
KNOWN_TOP_LEVEL_KEYS: frozenset[str] = frozenset(
    (set(LintroConfig.model_fields) - {"config_path"})
    | set(EXTERNALLY_HANDLED_SECTIONS)
    | set(STRUCTURAL_SECTIONS),
)

# Recognized keys within the ``execution`` section.
KNOWN_EXECUTION_KEYS: frozenset[str] = frozenset(ExecutionConfig.model_fields)

# Recognized keys within the ``enforce`` section.
KNOWN_ENFORCE_KEYS: frozenset[str] = frozenset(EnforceConfig.model_fields)

# Recognized keys within a per-tool ``tools.<name>`` mapping.
KNOWN_TOOL_KEYS: frozenset[str] = frozenset(LintroToolConfig.model_fields)

# Recognized keys within the remaining typed sections.
KNOWN_REVIEW_KEYS: frozenset[str] = frozenset(ReviewConfig.model_fields)
KNOWN_SCORE_KEYS: frozenset[str] = frozenset(ScoreConfig.model_fields)
KNOWN_OUTPUT_KEYS: frozenset[str] = frozenset(OutputConfig.model_fields)

# Sections whose typed parser calls ``.get``/``.items`` on its input without a
# type guard. YAML spells an empty section as ``enforce:`` which deserializes
# to ``None``, so these must be checked up front or the parser raises
# ``AttributeError`` out of the validator instead of reporting the problem.
# ``ai``/``review``/``score``/``output`` are excluded on purpose: their parsers
# accept ``None`` as "absent" and raise a descriptive ``ValueError`` otherwise.
MAPPING_SECTIONS: tuple[str, ...] = (
    "enforce",
    "execution",
    "defaults",
    "tools",
)

# Deprecated option names mapped to their modern replacement.
DEPRECATED_KEYS: dict[str, str] = {
    "line-length": "line_length",
    "target-python": "target_python",
    "global": "enforce",
}


def known_tool_names() -> frozenset[str]:
    """Return the set of recognized tool names.

    Reuses the loader's known-tool set so YAML ``tools:`` entries and
    pyproject tool tables accept the same names: ``ToolName`` (underscore
    and hyphen forms), legacy aliases such as ``markdownlint-cli2``, and
    installed plugin names from
    :func:`~lintro.plugins.discovery.get_known_plugin_tool_names`.

    Returns:
        frozenset[str]: Recognized tool identifiers.
    """
    return known_config_tool_names()


@dataclass
class ValidationMessage:
    """A single validation finding.

    Attributes:
        code: Stable machine-readable identifier for the kind of finding.
            ``--json`` consumers should branch on this rather than on
            ``message``, whose wording is not part of the contract.
        message: Human-readable description of the finding.
        location: Optional dotted path to the offending config key.
        suggestion: Optional corrected value (e.g. a ``did you mean`` hint).
    """

    code: ValidationCode
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
                    code=ValidationCode.DEPRECATED_OPTION,
                    # The YAML loader reads only the modern snake_case names,
                    # so a deprecated spelling is not merely dated: its value
                    # never reaches the config. Say so rather than implying it
                    # still works.
                    message="deprecated option, no longer applied",
                    location=location,
                    suggestion=DEPRECATED_KEYS[key],
                ),
            )
            continue
        warnings.append(
            ValidationMessage(
                code=ValidationCode.UNKNOWN_OPTION,
                message="unknown option",
                location=location,
                suggestion=_suggest(key, known),
            ),
        )


def _tool_value_type_error(
    name: str,
    tool_data: Any,
) -> ValidationMessage:
    """Build an INVALID_TYPE finding for a non-mapping, non-bool tool entry.

    Args:
        name: Tool name as written in the config.
        tool_data: The invalid value.

    Returns:
        ValidationMessage: Type error for ``tools.<name>``.
    """
    actual = "null" if tool_data is None else type(tool_data).__name__
    return ValidationMessage(
        code=ValidationCode.INVALID_TYPE,
        message=f"tool entry must be a mapping or boolean, got {actual}.",
        location=f"tools.{name}",
    )


def _check_tool_names(
    data: dict[str, Any],
    warnings: list[ValidationMessage],
    errors: list[ValidationMessage],
) -> None:
    """Warn about unknown tool names and reject non-mapping/non-bool entries.

    Args:
        data: The ``tools`` mapping.
        warnings: List to append unknown-tool findings to.
        errors: List to append type findings to.
    """
    known = known_tool_names()
    for name, tool_data in data.items():
        if name.lower() not in known:
            warnings.append(
                ValidationMessage(
                    code=ValidationCode.UNKNOWN_TOOL,
                    message=f"unknown tool '{name}'",
                    location="tools",
                    suggestion=_suggest(name.lower(), known),
                ),
            )
            continue
        if isinstance(tool_data, dict):
            _check_unknown_keys(
                tool_data,
                KNOWN_TOOL_KEYS,
                f"tools.{name}",
                warnings,
            )
            continue
        if isinstance(tool_data, bool):
            continue
        errors.append(_tool_value_type_error(name, tool_data))


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
                code=ValidationCode.UNKNOWN_TOOL,
                message=f"unknown tool '{name}'",
                location="execution.enabled_tools",
                suggestion=_suggest(name.lower(), known),
            ),
        )


def _check_section_types(
    parsed: dict[str, Any],
    errors: list[ValidationMessage],
) -> None:
    """Report sections that are present but are not mappings.

    YAML spells an empty section as ``enforce:`` with nothing under it, which
    deserializes to ``None``. The typed section parsers call ``.get`` on their
    input, so without this check the validator would raise ``AttributeError``
    instead of reporting the configuration as invalid.

    Args:
        parsed: The parsed configuration mapping.
        errors: List to append findings to.
    """
    for section in MAPPING_SECTIONS:
        if section not in parsed:
            continue
        value = parsed[section]
        if isinstance(value, dict):
            continue
        actual = "null" if value is None else type(value).__name__
        errors.append(
            ValidationMessage(
                code=ValidationCode.INVALID_TYPE,
                message=f"section must be a mapping, got {actual}.",
                location=section,
            ),
        )


def _not_found_result(
    config_path: Path | None,
    warnings: list[ValidationMessage] | None = None,
) -> ValidationResult:
    """Return the standard 'no config file' error result.

    Args:
        config_path: Path to record on the result, if any.
        warnings: Optional warnings to include (e.g. an ignored empty YAML).

    Returns:
        ValidationResult: Result with a ``NOT_FOUND`` error.
    """
    return ValidationResult(
        config_path=config_path,
        errors=[
            ValidationMessage(
                code=ValidationCode.NOT_FOUND,
                message=(
                    "No .lintro-config.yaml found. Run 'lintro init' to create one."
                ),
            ),
        ],
        warnings=list(warnings or []),
    )


def _is_empty_document(parsed: Any) -> bool:
    """Return whether a parsed YAML document is empty for loader purposes.

    ``load_config`` maps ``None`` and non-dicts to ``{}`` then treats a
    falsy mapping as "nothing found" and continues searching. Validate must
    not report success for those documents.

    Args:
        parsed: Value returned by ``yaml.safe_load``.

    Returns:
        bool: True when the document is ``None`` or ``{}``.
    """
    return parsed is None or parsed == {}


def _empty_yaml_error(config_path: Path) -> ValidationMessage:
    """Build the error for an empty YAML file given as an explicit path.

    Args:
        config_path: The empty file the caller asked to validate.

    Returns:
        ValidationMessage: ``EMPTY_CONFIG`` error describing runtime fallback.
    """
    return ValidationMessage(
        code=ValidationCode.EMPTY_CONFIG,
        message=(
            f"Config file {config_path} is empty and is not a successful "
            "config. load_config ignores empty YAML and still searches "
            "upward for another file or [tool.lintro]."
        ),
        location=str(config_path),
    )


def _parse_config_file(
    config_path: Path,
    result: ValidationResult,
) -> tuple[Any, bool] | None:
    """Read and parse a config file, recording parse errors on ``result``.

    Args:
        config_path: File to parse.
        result: Result to append parse errors to.

    Returns:
        tuple[Any, bool] | None: ``(parsed, is_pyproject)`` on success, or
            None when a parse error was recorded.
    """
    is_pyproject = config_path.name == "pyproject.toml"
    try:
        raw = config_path.read_text(encoding="utf-8")
        parsed = tomllib.loads(raw) if is_pyproject else yaml.safe_load(raw)
    except (OSError, yaml.YAMLError, tomllib.TOMLDecodeError) as exc:
        result.errors.append(
            ValidationMessage(
                code=ValidationCode.PARSE_ERROR,
                message=f"Could not parse config: {exc}",
            ),
        )
        return None
    return parsed, is_pyproject


def _pyproject_lintro_table(
    parsed: dict[str, Any],
    errors: list[ValidationMessage],
) -> dict[str, Any] | None:
    """Extract ``[tool.lintro]``, recording INVALID_TYPE for non-mappings.

    Args:
        parsed: Parsed pyproject.toml root table.
        errors: List to append type findings to.

    Returns:
        dict[str, Any] | None: The lintro table (possibly empty), or None
            when a type error was recorded and validation should stop.
    """
    if "tool" not in parsed:
        return {}
    tool = parsed["tool"]
    if not isinstance(tool, dict):
        actual = "null" if tool is None else type(tool).__name__
        errors.append(
            ValidationMessage(
                code=ValidationCode.INVALID_TYPE,
                message=f"'tool' must be a mapping, got {actual}.",
                location="tool",
            ),
        )
        return None
    if "lintro" not in tool:
        return {}
    lintro = tool["lintro"]
    if not isinstance(lintro, dict):
        actual = "null" if lintro is None else type(lintro).__name__
        errors.append(
            ValidationMessage(
                code=ValidationCode.INVALID_TYPE,
                message=f"'tool.lintro' must be a mapping, got {actual}.",
                location="tool.lintro",
            ),
        )
        return None
    return lintro


def _check_raw_pyproject_lintro(
    data: dict[str, Any],
    warnings: list[ValidationMessage],
    errors: list[ValidationMessage],
) -> None:
    """Run unknown-key and unknown-tool checks on the raw ``[tool.lintro]``.

    Must run before :func:`_convert_pyproject_to_config`, which drops
    unrecognized keys (logging only) so they would never reach validate
    output.

    Args:
        data: Raw ``[tool.lintro]`` table.
        warnings: List to append unknown-key/tool findings to.
        errors: List to append type findings to.
    """
    catalog = _pyproject_lintro_catalog()
    known_tools = catalog.known_tools
    reserved_keys = catalog.reserved_keys
    known = frozenset(known_tools)

    nested_tool_tables = ("tool", "tools")
    typed_sections: dict[str, frozenset[str]] = {
        "execution": KNOWN_EXECUTION_KEYS,
        "enforce": KNOWN_ENFORCE_KEYS,
        "review": KNOWN_REVIEW_KEYS,
        "score": KNOWN_SCORE_KEYS,
        "output": KNOWN_OUTPUT_KEYS,
    }

    for key, value in data.items():
        key_lower = key.lower()
        if key_lower in known_tools:
            # Top-level [tool.lintro.<tool>] holds native tool config, so
            # inner keys are not LintroToolConfig fields. Only the value
            # type is checked here.
            if not isinstance(value, dict) and not isinstance(value, bool):
                errors.append(_tool_value_type_error(key, value))
            continue
        if key_lower in nested_tool_tables:
            if not isinstance(value, dict):
                actual = "null" if value is None else type(value).__name__
                errors.append(
                    ValidationMessage(
                        code=ValidationCode.INVALID_TYPE,
                        message=(f"'{key_lower}' must be a mapping, got {actual}."),
                        location=f"tool.lintro.{key_lower}",
                    ),
                )
                continue
            _check_tool_names(value, warnings, errors)
            continue
        if key_lower in typed_sections and isinstance(value, dict):
            _check_unknown_keys(
                value,
                typed_sections[key_lower],
                key_lower,
                warnings,
            )
            continue
        if key_lower in reserved_keys or key.replace("-", "_") in reserved_keys:
            continue
        location = f"tool.lintro.{key}"
        if isinstance(value, dict):
            warnings.append(
                ValidationMessage(
                    code=ValidationCode.UNKNOWN_TOOL,
                    message=f"unknown tool '{key}'",
                    location="tool.lintro",
                    suggestion=_suggest(key_lower, known),
                ),
            )
            continue
        warnings.append(
            ValidationMessage(
                code=ValidationCode.UNKNOWN_OPTION,
                message="unknown option",
                location=location,
                suggestion=_suggest(key_lower, frozenset(reserved_keys) | known),
            ),
        )


def _schema_check_normalized(
    parsed: dict[str, Any],
    result: ValidationResult,
    *,
    skip_unknown_keys: bool,
) -> None:
    """Apply schema warnings and typed-parser checks to a normalized mapping.

    Args:
        parsed: YAML-shaped configuration mapping.
        result: Result to append findings to.
        skip_unknown_keys: When True, skip unknown-key/tool walks that were
            already performed on the raw pyproject table.
    """
    if not skip_unknown_keys:
        _check_unknown_keys(parsed, KNOWN_TOP_LEVEL_KEYS, "", result.warnings)

    execution = parsed.get("execution")
    if isinstance(execution, dict):
        if not skip_unknown_keys:
            _check_unknown_keys(
                execution,
                KNOWN_EXECUTION_KEYS,
                "execution",
                result.warnings,
            )
        _check_enabled_tools(execution, result.warnings)

    if not skip_unknown_keys:
        enforce = parsed.get("enforce")
        if isinstance(enforce, dict):
            _check_unknown_keys(
                enforce,
                KNOWN_ENFORCE_KEYS,
                "enforce",
                result.warnings,
            )

        for section, known in (
            ("review", KNOWN_REVIEW_KEYS),
            ("score", KNOWN_SCORE_KEYS),
            ("output", KNOWN_OUTPUT_KEYS),
        ):
            data = parsed.get(section)
            if isinstance(data, dict):
                _check_unknown_keys(data, known, section, result.warnings)

        tools = parsed.get("tools")
        if isinstance(tools, dict):
            _check_tool_names(tools, result.warnings, result.errors)

    _check_section_types(parsed, result.errors)
    if result.errors:
        # The typed parsers below assume mappings; running them on a null or
        # scalar section raises instead of reporting the problem.
        return

    # Run the real typed parsers to catch value errors (max_fix_retries,
    # auto_install, review schema, etc.) against the requested file. The
    # already-parsed mapping is fed straight through ``build_config_from_dict``
    # rather than via ``load_config``: the latter treats a falsy config (an
    # empty ``{}`` document) as "nothing found" and silently falls back to
    # auto-discovery, which would validate a different file than the one asked
    # for. ``load_config`` also cannot read pyproject.toml from an explicit
    # path, since it reads explicit paths as YAML.
    try:
        build_config_from_dict(parsed)
    except (ValueError, TypeError, AttributeError) as exc:
        result.errors.append(
            ValidationMessage(code=ValidationCode.INVALID_TYPE, message=str(exc)),
        )


def validate_config_file(path: Path | str | None = None) -> ValidationResult:
    """Validate a Lintro configuration file.

    Loads the config (or locates one by searching upward), checks it against
    the known schema, and reports both hard errors and softer warnings.

    Empty YAML (``None`` or ``{}``) is not a successful config: ``load_config``
    ignores it and continues to ``[tool.lintro]``. Auto-detect therefore
    falls through to pyproject the same way; an explicit empty path is
    reported as ``EMPTY_CONFIG`` so validate never exits 0 while runtime
    would load a different file.

    Args:
        path: Explicit path to a config file. When None, the nearest
            ``.lintro-config.yaml`` is located by searching upward, then
            ``[tool.lintro]`` in ``pyproject.toml``, mirroring
            :func:`lintro.config.config_loader.load_config`.

    Returns:
        ValidationResult: Structured validation outcome.
    """
    explicit = path is not None
    if path is not None:
        config_path = Path(path)
        if not config_path.exists():
            return ValidationResult(
                config_path=config_path,
                errors=[
                    ValidationMessage(
                        code=ValidationCode.NOT_FOUND,
                        message=f"Config file not found: {config_path}",
                    ),
                ],
            )
    else:
        found = _find_config_file()
        if found is None:
            # Locate pyproject.toml by path, then parse it ourselves. The
            # loader's ``_load_pyproject_fallback`` returns ``{}, None`` on
            # TOML errors, which would look like NOT_FOUND; validate must
            # fail closed with PARSE_ERROR instead.
            found = _find_pyproject()
        if found is None:
            return _not_found_result(config_path=None)
        config_path = found

    result = ValidationResult(config_path=config_path)

    if yaml is None:  # pragma: no cover - enforced by packaging
        result.errors.append(
            ValidationMessage(
                code=ValidationCode.MISSING_DEPENDENCY,
                message="PyYAML is required to validate configuration.",
            ),
        )
        return result

    parsed_pair = _parse_config_file(config_path, result)
    if parsed_pair is None:
        return result
    parsed, is_pyproject = parsed_pair

    if not is_pyproject and _is_empty_document(parsed):
        if explicit:
            result.errors.append(_empty_yaml_error(config_path))
            return result
        # Auto-detect: treat empty YAML like the loader — continue to
        # pyproject rather than reporting VALID for a file runtime ignores.
        result.warnings.append(
            ValidationMessage(
                code=ValidationCode.EMPTY_CONFIG,
                message=(
                    f"{config_path} is empty and will be ignored at runtime; "
                    "continuing to [tool.lintro] in pyproject.toml."
                ),
                location=str(config_path),
            ),
        )
        pyproject_path = _find_pyproject()
        if pyproject_path is None:
            return _not_found_result(
                config_path=config_path,
                warnings=result.warnings,
            )
        config_path = pyproject_path
        result.config_path = config_path
        parsed_pair = _parse_config_file(config_path, result)
        if parsed_pair is None:
            return result
        parsed, is_pyproject = parsed_pair

    if not isinstance(parsed, dict):
        result.errors.append(
            ValidationMessage(
                code=ValidationCode.INVALID_TYPE,
                message=(
                    f"Config root must be a mapping, got {type(parsed).__name__}."
                ),
            ),
        )
        return result

    if is_pyproject:
        lintro = _pyproject_lintro_table(parsed, result.errors)
        if result.errors:
            return result
        if not lintro:
            if explicit:
                result.errors.append(
                    ValidationMessage(
                        code=ValidationCode.EMPTY_CONFIG,
                        message=(
                            "pyproject.toml has no [tool.lintro] table; "
                            "this is not a successful Lintro config."
                        ),
                    ),
                )
                return result
            return _not_found_result(
                config_path=config_path,
                warnings=result.warnings,
            )
        _check_raw_pyproject_lintro(lintro, result.warnings, result.errors)
        if result.errors:
            return result
        parsed = _convert_pyproject_to_config(lintro)
        _schema_check_normalized(parsed, result, skip_unknown_keys=True)
        return result

    _schema_check_normalized(parsed, result, skip_unknown_keys=False)
    return result

"""Stable machine-readable codes for configuration validation findings.

These codes are part of the ``lintro config validate --json`` contract:
consumers should branch on ``code`` rather than on the human-readable
``message`` text, which may be reworded at any time.
"""

from enum import StrEnum


class ValidationCode(StrEnum):
    """Stable identifiers for configuration validation findings.

    Values are lowercase snake_case and form the JSON ``code`` vocabulary
    for ``lintro config validate --json``. Adding a member is a
    backwards-compatible change; renaming one is not.

    Attributes:
        NOT_FOUND: No config file was found at the requested or detected path.
        PARSE_ERROR: The config file could not be parsed as YAML or TOML.
        EMPTY_CONFIG: The file exists but has no usable Lintro configuration.
        INVALID_TYPE: A value has the wrong type or is otherwise unusable.
        UNKNOWN_OPTION: A key is not a recognized configuration option.
        DEPRECATED_OPTION: A key is recognized but no longer applied.
        UNKNOWN_TOOL: A tool name is not in the known tool set.
        MISSING_DEPENDENCY: A required helper or section is missing.
    """

    NOT_FOUND = "not_found"
    PARSE_ERROR = "parse_error"
    EMPTY_CONFIG = "empty_config"
    INVALID_TYPE = "invalid_type"
    UNKNOWN_OPTION = "unknown_option"
    DEPRECATED_OPTION = "deprecated_option"
    UNKNOWN_TOOL = "unknown_tool"
    MISSING_DEPENDENCY = "missing_dependency"

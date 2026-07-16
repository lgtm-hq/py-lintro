"""Option validation utilities for tool plugins.

This module provides common validation functions to reduce boilerplate
in tool set_options() methods.
"""

from collections.abc import Mapping
from typing import Any

# Human-friendly labels used when reporting an option's expected type.
# These keep error messages consistent with the historical wording
# (e.g. "must be a boolean" rather than "must be a bool").
_TYPE_LABELS: dict[type, str] = {
    bool: "boolean",
    str: "string",
    int: "integer",
    float: "number",
    list: "list",
}

# A single option's expected type. Either a bare ``type`` (whose label is
# looked up in ``_TYPE_LABELS``) or a ``(type, label)`` tuple that overrides
# the label used in the error message (e.g. ``(str, "string path")``).
OptionType = type | tuple[type, str]

# Mapping of option name to its expected type specification.
OptionSchema = Mapping[str, OptionType]


def _resolve_option_type(spec: OptionType) -> tuple[type, str]:
    """Resolve an option type specification into a type and its label.

    Args:
        spec: Either a bare ``type`` or a ``(type, label)`` tuple.

    Returns:
        tuple[type, str]: The expected type and its human-friendly label.
    """
    if isinstance(spec, tuple):
        expected_type, label = spec
        return expected_type, label
    return spec, _TYPE_LABELS.get(spec, spec.__name__)


def _type_error_message(name: str, label: str) -> str:
    """Build a type-mismatch error message with a grammatical article.

    Args:
        name: The option name.
        label: The human-friendly type label (e.g. "boolean", "integer").

    Returns:
        str: An error message such as "name must be a boolean" or
        "name must be an integer".
    """
    article = "an" if label[:1].lower() in "aeiou" else "a"
    return f"{name} must be {article} {label}"


def validate_option_types(
    options: Mapping[str, Any],
    schema: OptionSchema,
) -> None:
    """Validate option values against a declarative type schema.

    Iterates over the schema and checks each present (non-``None``) option
    against its expected type, raising a ``ValueError`` naming the offending
    option on the first mismatch. Options absent from ``options`` or set to
    ``None`` are skipped, and options not present in the schema are ignored
    (pass-through), preserving the historical per-option validation behavior.

    Args:
        options: Mapping of option name to provided value.
        schema: Mapping of option name to its expected type specification.

    Raises:
        ValueError: If a present option's value is not of its expected type.
    """
    for name, spec in schema.items():
        value = options.get(name)
        if value is None:
            continue
        expected_type, label = _resolve_option_type(spec)
        # ``bool`` is a subclass of ``int``; reject booleans where an integer
        # is required so numeric options do not silently accept True/False.
        if expected_type is int and isinstance(value, bool):
            raise ValueError(_type_error_message(name, label))
        if not isinstance(value, expected_type):
            raise ValueError(_type_error_message(name, label))


def validate_bool(value: Any, name: str) -> None:
    """Validate that value is a boolean if not None.

    Args:
        value: Value to validate.
        name: Parameter name for error message.

    Raises:
        ValueError: If value is not None and not a boolean.
    """
    if value is not None and not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")


def validate_str(value: Any, name: str) -> None:
    """Validate that value is a string if not None.

    Args:
        value: Value to validate.
        name: Parameter name for error message.

    Raises:
        ValueError: If value is not None and not a string.
    """
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{name} must be a string")


def validate_int(
    value: Any,
    name: str,
    min_value: int | None = None,
    max_value: int | None = None,
) -> None:
    """Validate that value is an integer if not None.

    Args:
        value: Value to validate.
        name: Parameter name for error message.
        min_value: Optional minimum allowed value (inclusive).
        max_value: Optional maximum allowed value (inclusive).

    Raises:
        ValueError: If value is not None and not an integer, or if value
            is outside the specified range.
    """
    if value is None:
        return

    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")

    if min_value is not None and value < min_value:
        raise ValueError(f"{name} must be at least {min_value}")

    if max_value is not None and value > max_value:
        raise ValueError(f"{name} must be at most {max_value}")


def validate_positive_int(value: Any, name: str) -> None:
    """Validate that value is a positive integer if not None.

    Args:
        value: Value to validate.
        name: Parameter name for error message.

    Raises:
        ValueError: If value is not None and not a positive integer.
    """
    if value is not None:
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"{name} must be an integer")
        if value <= 0:
            raise ValueError(f"{name} must be positive")


def validate_list(value: Any, name: str) -> None:
    """Validate that value is a list if not None.

    Args:
        value: Value to validate.
        name: Parameter name for error message.

    Raises:
        ValueError: If value is not None and not a list.
    """
    if value is not None and not isinstance(value, list):
        raise ValueError(f"{name} must be a list")


def normalize_str_or_list(value: Any, name: str) -> list[str] | None:
    """Normalize a string or list value to a list.

    Args:
        value: Value to normalize (string, list, or None).
        name: Parameter name for error message.

    Returns:
        List of strings, or None if input was None.

    Raises:
        ValueError: If value is not None, string, or list.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        if not all(isinstance(item, str) for item in value):
            raise ValueError(f"{name} must be a string or list of strings")
        return value
    raise ValueError(f"{name} must be a string or list")


def filter_none_options(**kwargs: Any) -> dict[str, Any]:
    """Filter out None values from keyword arguments.

    Args:
        **kwargs: Keyword arguments to filter.

    Returns:
        Dictionary with only non-None values.
    """
    return {k: v for k, v in kwargs.items() if v is not None}

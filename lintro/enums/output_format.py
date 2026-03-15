"""Output format enum definitions.

This module defines the supported output formats for displaying results.
"""

from __future__ import annotations

from enum import StrEnum, auto

from loguru import logger


class OutputFormat(StrEnum):
    """Supported output formats for rendering results.

    Values are lower-case string identifiers to align with CLI choices.
    """

    PLAIN = auto()
    GRID = auto()
    MARKDOWN = auto()
    HTML = auto()
    JSON = auto()
    CSV = auto()
    GITHUB = auto()
    SARIF = auto()


def normalize_output_format(value: str | OutputFormat) -> OutputFormat:
    """Normalize a raw value to an OutputFormat enum.

    Args:
        value: str or OutputFormat to normalize.

    Returns:
        OutputFormat: Normalized enum value.
    """
    if isinstance(value, OutputFormat):
        return value
    try:
        return OutputFormat[value.upper()]
    except (KeyError, AttributeError) as e:
        logger.debug(f"Invalid OutputFormat value '{value}': {e}. Defaulting to GRID.")
        return OutputFormat.GRID

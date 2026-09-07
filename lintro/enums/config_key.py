"""Configuration key definitions.

Provides canonical identifiers for configuration keys used across the codebase.
"""

from enum import StrEnum, auto


class ConfigKey(StrEnum):
    """Supported configuration key identifiers."""

    VERSIONS = auto()
    DEFAULTS = auto()

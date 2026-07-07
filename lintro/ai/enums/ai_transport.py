"""AI transport enumeration for API vs CLI invocation."""

from __future__ import annotations

from enum import auto

from lintro.enums.hyphenated_str_enum import HyphenatedStrEnum

__all__ = ["AITransport"]


class AITransport(HyphenatedStrEnum):
    """How lintro invokes the configured AI provider."""

    API = auto()
    CLI = auto()

"""AI provider enumeration.

Defines the ``AIProvider`` :class:`~enum.StrEnum` that identifies each
supported AI backend.  Extracted from :mod:`lintro.ai.registry` so that
lightweight modules can reference provider identities without pulling in
the full registry and its pricing data.
"""

from __future__ import annotations

from enum import StrEnum, auto


class AIProvider(StrEnum):
    """Supported AI providers."""

    ANTHROPIC = auto()
    OPENAI = auto()

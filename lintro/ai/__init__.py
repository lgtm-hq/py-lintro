"""AI-powered issue intelligence for Lintro.

This package provides optional AI capabilities for summarizing, fixing,
and prioritizing linting issues. Requires installation with AI extras:

    uv pip install 'lintro[ai]'

Features:
    - AI-powered summaries with pattern detection and priority actions
    - Fix generation for issues that native tools can't auto-fix
    - Interactive suggestion review mode
    - Triage guidance for suppressing vs fixing issues

Exports are resolved lazily so importing leaf modules (e.g.
``lintro.ai.config``) does not pull the full AI surface at cold start.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lintro.ai.availability import is_ai_available as is_ai_available
    from lintro.ai.availability import require_ai as require_ai
    from lintro.ai.config import AIConfig as AIConfig
    from lintro.ai.enums import ConfidenceLevel as ConfidenceLevel
    from lintro.ai.enums import RiskLevel as RiskLevel
    from lintro.ai.models import AIResult as AIResult
    from lintro.ai.provider_enum import AIProvider as AIProvider

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "AIConfig": ("lintro.ai.config", "AIConfig"),
    "AIProvider": ("lintro.ai.provider_enum", "AIProvider"),
    "AIResult": ("lintro.ai.models", "AIResult"),
    "ConfidenceLevel": ("lintro.ai.enums", "ConfidenceLevel"),
    "RiskLevel": ("lintro.ai.enums", "RiskLevel"),
    "is_ai_available": ("lintro.ai.availability", "is_ai_available"),
    "require_ai": ("lintro.ai.availability", "require_ai"),
}

__all__ = [
    "AIConfig",
    "AIProvider",
    "AIResult",
    "ConfidenceLevel",
    "RiskLevel",
    "is_ai_available",
    "require_ai",
]


def __getattr__(name: str) -> Any:
    """Resolve public AI exports on first access.

    Args:
        name: Attribute name being accessed.

    Returns:
        The lazily imported attribute.

    Raises:
        AttributeError: If ``name`` is not a public export.
    """
    if name not in _LAZY_EXPORTS:
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg)
    module_name, attr_name = _LAZY_EXPORTS[name]
    module = __import__(module_name, fromlist=[attr_name])
    value = getattr(module, attr_name)
    globals()[name] = value
    return value

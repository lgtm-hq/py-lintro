"""AI-powered ``idiom-review`` tool package.

Everything the ``idiom-review`` tool owns lives here: the plugin and its
:class:`~lintro.plugins.protocol.ToolDefinition` in
:mod:`lintro.tools.idiom_review.definition`, plus the prompt templates,
signature extraction, and the AI-calling engine it delegates to. These live in
their own package (rather than under ``lintro/ai/prompts``) so the tool is
self-contained and its prompt surface can evolve independently.
``lintro.tools.definitions.idiom_review`` re-exports the plugin so plugin
discovery keeps finding it (#2311).

:class:`~lintro.tools.idiom_review.engine.IdiomReviewEngine` is re-exported
lazily: importing it eagerly here would pull :mod:`lintro.ai` into plugin
discovery, and therefore into every ``lintro chk`` invocation (#1305 /
#1308).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lintro.enums.idiom_review_mode import IdiomReviewMode
from lintro.tools.idiom_review.definition import (
    IDIOM_REVIEW_DEFAULT_MAX_FILES,
    IDIOM_REVIEW_DEFAULT_TIMEOUT,
    IDIOM_REVIEW_FILE_PATTERNS,
    IDIOM_REVIEW_PRIORITY,
    IDIOM_REVIEW_TOOL_NAME,
    IdiomReviewPlugin,
)

if TYPE_CHECKING:
    from lintro.tools.idiom_review.engine import IdiomReviewEngine

__all__ = [
    "IDIOM_REVIEW_DEFAULT_MAX_FILES",
    "IDIOM_REVIEW_DEFAULT_TIMEOUT",
    "IDIOM_REVIEW_FILE_PATTERNS",
    "IDIOM_REVIEW_PRIORITY",
    "IDIOM_REVIEW_TOOL_NAME",
    "IdiomReviewEngine",
    "IdiomReviewMode",
    "IdiomReviewPlugin",
]


def __getattr__(name: str) -> Any:
    """Resolve lazily re-exported names on first access.

    Args:
        name: Attribute name being looked up on this module.

    Returns:
        The resolved attribute.

    Raises:
        AttributeError: If the name is not a lazy re-export of this package.
    """
    if name == "IdiomReviewEngine":
        from lintro.tools.idiom_review.engine import IdiomReviewEngine

        return IdiomReviewEngine
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

"""Support modules for the AI-powered ``idiom-review`` tool.

Groups the prompt templates, signature extraction, and the AI-calling
engine used by :mod:`lintro.tools.definitions.idiom_review`. These live in
their own package (rather than under ``lintro/ai/prompts``) so the tool is
self-contained and its prompt surface can evolve independently.

:class:`~lintro.tools.idiom_review.engine.IdiomReviewEngine` is re-exported
lazily: importing it eagerly here would pull :mod:`lintro.ai` into plugin
discovery, and therefore into every ``lintro chk`` invocation (#1305 /
#1308).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lintro.enums.idiom_review_mode import IdiomReviewMode

if TYPE_CHECKING:
    from lintro.tools.idiom_review.engine import IdiomReviewEngine

__all__ = ["IdiomReviewEngine", "IdiomReviewMode"]


def __getattr__(name: str) -> Any:  # noqa: ANN401 - module-level lazy re-export
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

"""Support modules for the AI-powered ``idiom-review`` tool.

Groups the prompt templates, signature extraction, and the AI-calling
engine used by :mod:`lintro.tools.definitions.idiom_review`. These live in
their own package (rather than under ``lintro/ai/prompts``) so the tool is
self-contained and its prompt surface can evolve independently.
"""

from __future__ import annotations

from lintro.tools.idiom_review.engine import (
    IdiomReviewEngine,
    IdiomReviewMode,
)

__all__ = ["IdiomReviewEngine", "IdiomReviewMode"]

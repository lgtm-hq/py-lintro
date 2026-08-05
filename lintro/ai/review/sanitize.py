"""Sanitization of untrusted model text bound for GitHub comment surfaces.

Lives below both the renderers and the prompt builders: every surface that
embeds model output needs it, and keeping it here is what lets the finding
renderer render a prompt panel without the two modules importing each other.
"""

from __future__ import annotations

from lintro.ai.review.github_constants import _MENTION_RE

__all__ = ["sanitize_comment_text"]


def sanitize_comment_text(text: str, *, limit: int | None = None) -> str:
    """Neutralize untrusted model output for safe rendering in a PR comment.

    Breaks GitHub ``@mentions`` (so injected text cannot ping or notify users)
    by inserting a zero-width space after a leading ``@``, and optionally caps
    the length. The input originates from an untrusted PR diff, so this is a
    security boundary, not cosmetic.

    Args:
        text: Raw model-derived text.
        limit: Optional maximum character length before truncation.

    Returns:
        Sanitized text safe to embed in Markdown.
    """
    cleaned = _MENTION_RE.sub("@​", text or "")
    if limit is not None and len(cleaned) > limit:
        cleaned = cleaned[: max(limit - 1, 0)].rstrip() + "…"
    return cleaned

"""Token-aware budget estimation and truncation utilities.

Provides a simple character-based token estimator (4 chars ~ 1 token)
and truncation helpers used by summary and fix prompt builders to stay
within model context limits.
"""

from __future__ import annotations


def estimate_tokens(text: str) -> int:
    """Estimate token count from text (4 chars ~ 1 token).

    Args:
        text: Input text to estimate.

    Returns:
        Estimated token count (minimum 1 for non-empty text, 0 for empty).
    """
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


def truncate_to_budget(text: str, max_tokens: int) -> tuple[str, bool]:
    """Truncate text to fit within a token budget.

    Cuts at the last newline boundary before the character limit so that
    the result remains readable.

    Args:
        text: Text to truncate.
        max_tokens: Maximum allowed tokens.

    Returns:
        Tuple of (possibly truncated text, was_truncated).
    """
    if max_tokens <= 0:
        raise ValueError(f"max_tokens must be positive, got {max_tokens}")
    if estimate_tokens(text) <= max_tokens:
        return text, False

    max_chars = max_tokens * 4
    # Try to cut at a line boundary for readability
    truncated = text[:max_chars]
    last_newline = truncated.rfind("\n")
    if last_newline > 0:
        truncated = truncated[:last_newline]

    return truncated, True

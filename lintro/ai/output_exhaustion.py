"""Recognise a provider answer that overran its output-token ceiling.

A call whose JSON document crossed the provider's output limit (#1967) is not
a transient failure: repeating the same request produces the same overrun.
The retry seam in :mod:`lintro.ai.retry` therefore raises such errors on the
first attempt, and :mod:`lintro.ai.review.chunk_split_retry` answers them by
making the unit of work smaller. Kept free of config imports so both can use
it.
"""

from __future__ import annotations

import re

__all__ = ["is_output_exhaustion_error"]

#: Structured completion fields a provider emits when the answer itself was
#: cut off. These are trusted on their own.
_STRUCTURED_NEEDLES = (
    'stop_reason":"max_tokens',
    'stop_reason":"length',
    'finish_reason":"length',
)

#: Human-readable phrasings of the same failure. A request the provider
#: rejected (a 400 for an invalid ``max_tokens``, a 429) can quote these words
#: too, and splitting the input cannot repair either (#2695), so prose counts
#: only when the message carries no API-error marker.
_PROSE_NEEDLES = (
    "max output tokens",
    "maximum output tokens",
    "output token limit",
    "maximum number of output tokens",
)

#: Markers of a rejected request rather than a truncated answer: the Claude
#: CLI's API-error envelope fields (``"terminal_reason":"api_error"`` with
#: ``api_error_status``), the vendors' error types, and HTTP status phrasing.
_API_ERROR_MARKERS = (
    "api_error_status",
    'terminal_reason":"api_error',
    "invalid_request_error",
    "bad request",
    "too many requests",
    "rate limit",
)
#: A 4xx status counts only in status position (message start, or after
#: ``http``/``status``/``code``/``error``): a bare number also appears in
#: token counts ("reached after 400 tokens") and model names.
_HTTP_STATUS_RE = re.compile(
    r"(?:^|\b(?:http|status|status_code|code|error)\b\W{0,3})"
    r"(?:400|401|403|404|422|429)\b",
)


def _carries_api_error_marker(text: str) -> bool:
    """Return whether normalised *text* names a rejected request.

    Args:
        text: Lower-cased, JSON-normalised provider message.

    Returns:
        True when an API-error field, error type or 4xx status is present.
    """
    if any(marker in text for marker in _API_ERROR_MARKERS):
        return True
    return _HTTP_STATUS_RE.search(text) is not None


def is_output_exhaustion_error(message: str) -> bool:
    """Return True when *message* looks like a mid-JSON 32k output failure.

    Args:
        message: Exception message from a provider call.

    Returns:
        True when the message carries a structured completion signal, or a
        known output-cap phrase without any API-error marker (#2695).
    """
    # Normalize JSON spacing so needle matching is layout-independent.
    text = message.lower().replace('": "', '":"')
    if any(needle in text for needle in _STRUCTURED_NEEDLES):
        return True
    # Restrictive on purpose: every Claude CLI failure envelope contains
    # generic tokens like ``is_error``, ``output_tokens`` (usage block), and
    # ``finish_reason`` — matching those would classify *any* provider error
    # (auth, timeout, 4xx) as output exhaustion and skip retries that would
    # have helped. Output-specific phrasings only: "exceeded the maximum
    # number of tokens" also matches INPUT context-window violations, and
    # "response truncated" matches generic proxy/stream errors (#1967 review).
    if _carries_api_error_marker(text):
        return False
    return any(needle in text for needle in _PROSE_NEEDLES)

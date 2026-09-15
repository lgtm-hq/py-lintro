"""Recognise a provider answer that overran its output-token ceiling.

A call whose JSON document crossed the provider's output limit (#1967) is not
a transient failure: repeating the same request produces the same overrun.
The retry seam in :mod:`lintro.ai.retry` therefore raises such errors on the
first attempt, and :mod:`lintro.ai.review.chunk_split_retry` answers them by
making the unit of work smaller. Kept free of config imports so both can use
it.
"""

from __future__ import annotations

__all__ = ["is_output_exhaustion_error"]


def is_output_exhaustion_error(message: str) -> bool:
    """Return True when *message* looks like a mid-JSON 32k output failure.

    Args:
        message: Exception message from a provider call.

    Returns:
        True when the message matches known output-cap exhaustion signatures.
    """
    # Normalize JSON spacing so needle matching is layout-independent.
    text = message.lower().replace('": "', '":"')
    # Restrictive on purpose: every Claude CLI failure envelope contains
    # generic tokens like ``is_error``, ``output_tokens`` (usage block), and
    # ``finish_reason`` — matching those would classify *any* provider error
    # (auth, timeout, 4xx) as output exhaustion and skip retries that would
    # have helped. Output-specific phrasings only: "exceeded the maximum
    # number of tokens" also matches INPUT context-window violations, and
    # "response truncated" matches generic proxy/stream errors (#1967 review).
    needles = (
        'stop_reason":"max_tokens',
        'stop_reason":"length',
        'finish_reason":"length',
        "max output tokens",
        "maximum output tokens",
        "output token limit",
        "maximum number of output tokens",
    )
    return any(needle in text for needle in needles)

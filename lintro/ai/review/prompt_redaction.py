"""Single secret-redaction choke point for every review prompt path.

Extracted from :mod:`lintro.ai.review.orchestrator` so the built-in checklist
passes and the custom review agent passes (issue #1245) share one
implementation instead of each growing their own.
"""

from __future__ import annotations

from loguru import logger

from lintro.ai.secrets import redact_secrets, scan_for_secrets

__all__ = ["redact_prompt_text"]


def redact_prompt_text(*, text: str, source: str) -> str:
    """Redact detected secrets from prompt text before provider dispatch.

    Every diff, PR metadata blob, or maintainer-authored agent body destined
    for an external AI provider passes through here so credentials never leave
    the machine.

    Args:
        text: Raw text destined for an AI provider prompt.
        source: Human-readable label for the text origin, used in log lines.

    Returns:
        The text with any detected secrets replaced by ``[REDACTED]``.
    """
    detected = scan_for_secrets(text)
    if detected:
        logger.warning(
            "Redacted {count} potential secret(s) from review {source} "
            "before sending to the AI provider.",
            count=len(detected),
            source=source,
        )
    return redact_secrets(text)

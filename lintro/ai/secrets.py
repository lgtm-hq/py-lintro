"""Detect potential secrets in code before sending to AI."""

from __future__ import annotations

import re

# Common secret patterns
_SECRET_PATTERNS = [
    re.compile(
        r"(?:api[_-]?key|apikey)\s*[=:]\s*[\"']?[A-Za-z0-9_\-]{20,}",
        re.I,
    ),
    re.compile(
        r"(?:secret|password|passwd|pwd)\s*[=:]\s*[\"']?[^\s\"']{8,}",
        re.I,
    ),
    re.compile(
        r"(?:token)\s*[=:]\s*[\"']?[A-Za-z0-9_\-\.]{20,}",
        re.I,
    ),
    re.compile(
        r"(?:aws_access_key_id|aws_secret_access_key)\s*[=:]\s*[\"']?[A-Za-z0-9/+=]{16,}",
        re.I,
    ),
    re.compile(r"ghp_[A-Za-z0-9]{36}"),  # GitHub personal access token
    re.compile(r"sk-[A-Za-z0-9]{20,}"),  # OpenAI/Anthropic API key
    re.compile(
        r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----"
        r"[\s\S]*?"
        r"-----END (?:RSA |EC )?PRIVATE KEY-----",
    ),
]


def scan_for_secrets(text: str) -> list[str]:
    """Return list of detected secret pattern descriptions.

    Scans the given text against a set of common secret patterns
    (API keys, passwords, tokens, private keys) and returns a
    human-readable description for each match found.

    Args:
        text: The text to scan for secrets.

    Returns:
        List of description strings for each detected secret pattern.
    """
    found: list[str] = []
    for pattern in _SECRET_PATTERNS:
        if pattern.search(text):
            found.append(f"Potential secret detected: {pattern.pattern[:40]}...")
    return found


def redact_secrets(text: str) -> str:
    """Redact detected secrets from text.

    Replaces all matches of known secret patterns with ``[REDACTED]``
    to prevent accidental leakage when sending text to external AI
    providers.

    Args:
        text: The text to redact secrets from.

    Returns:
        Text with all detected secrets replaced by ``[REDACTED]``.
    """
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text

"""Prompt injection hardening for AI fix generation.

Sanitizes code content before it is inserted into AI prompts to
mitigate prompt injection attacks. This is a defense-in-depth
measure — it neutralizes common injection patterns without altering
valid source code semantics.
"""

from __future__ import annotations

import re
import uuid

# Boundary marker used to fence code content in prompts.
# A per-call unique token is appended at runtime so that
# attacker-controlled content cannot predict or replicate it.
_BOUNDARY_PREFIX = "CODE_BLOCK"

# Patterns that look like attempts to break out of the code context
# and inject new instructions.  Each tuple is (compiled regex, label).
_INJECTION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Direct instruction overrides
    (
        re.compile(
            r"(?:^|\n)\s*(?:ignore|disregard|forget)\s+"
            r"(?:all\s+)?(?:previous|prior|above|earlier)\s+"
            r"(?:instructions?|context|prompts?|rules?)",
            re.I,
        ),
        "instruction-override",
    ),
    # Attempts to impersonate system / assistant role boundaries
    (
        re.compile(r"(?:^|\n)\s*(?:system|assistant|user)\s*:", re.I),
        "role-impersonation",
    ),
    # XML-style tags that could confuse structured prompts
    # (but NOT common HTML tags like <div>, <span>, <p>, etc.)
    (
        re.compile(
            r"</?(?:system|instruction|prompt|command|tool_call"
            r"|function_call|assistant|user)(?:\s[^>]*)?>",
            re.I,
        ),
        "xml-tag-injection",
    ),
    # Markdown heading-style instruction injection
    (
        re.compile(
            r"(?:^|\n)#{1,3}\s*(?:new\s+)?(?:system\s+)?instructions?",
            re.I,
        ),
        "heading-injection",
    ),
]

# Characters used to escape role-boundary patterns inside code content.
# We insert a zero-width space (U+200B) after the colon in "system:" etc.
# so that the AI does not interpret them as role markers.
_ZERO_WIDTH_SPACE = "\u200b"


def _neutralize_role_markers(text: str) -> str:
    """Insert a zero-width space after role-like prefixes.

    Transforms patterns like ``system:`` into ``system:\u200b`` so the
    AI provider does not misinterpret them as role boundaries.  Only
    matches at the start of a line (with optional leading whitespace).

    Args:
        text: The text to process.

    Returns:
        Text with role markers neutralized.
    """
    return re.sub(
        r"(?m)(^[ \t]*(?:system|assistant|user))\s*:",
        rf"\1:{_ZERO_WIDTH_SPACE}",
        text,
        flags=re.IGNORECASE,
    )


def _neutralize_xml_tags(text: str) -> str:
    """Escape XML-like tags that could confuse the model's parsing.

    Replaces the opening ``<`` with ``<`` only for tags whose names
    match known prompt-structural elements (system, instruction, etc.).

    Args:
        text: The text to process.

    Returns:
        Text with dangerous XML tags escaped.
    """
    return re.sub(
        r"<(/?(?:system|instruction|prompt|command|tool_call"
        r"|function_call|assistant|user)(?:\s[^>]*)?)>",
        r"&lt;\1>",
        text,
        flags=re.IGNORECASE,
    )


def sanitize_code_content(content: str) -> str:
    """Sanitize code content before inserting it into an AI prompt.

    Applies lightweight transformations that neutralize common prompt
    injection vectors without changing the semantic meaning of valid
    source code:

    * Role-boundary markers (``system:``, ``assistant:``) are broken
      with a zero-width space so the model does not treat them as
      role switches.
    * XML-like tags matching prompt-structural names are escaped.
    * The content is otherwise returned unchanged — ordinary code
      that happens to contain words like "system" or "ignore" in
      variable names or comments is not affected.

    Args:
        content: Raw code content to sanitize.

    Returns:
        Sanitized content safe for prompt insertion.
    """
    if not content:
        return content

    result = _neutralize_role_markers(content)
    result = _neutralize_xml_tags(result)
    return result


def detect_injection_patterns(content: str) -> list[str]:
    """Detect potential prompt injection patterns in content.

    Returns a list of labels for each injection pattern detected.
    This is intended for logging/auditing — it does NOT block the
    content from being sent.

    Args:
        content: The text to scan.

    Returns:
        List of injection pattern labels found (empty if clean).
    """
    found: list[str] = []
    for pattern, label in _INJECTION_PATTERNS:
        if pattern.search(content):
            found.append(label)
    return found


def make_boundary_marker() -> str:
    """Generate a unique boundary marker for code fencing.

    Returns a string like ``CODE_BLOCK_a1b2c3d4`` that can be used as
    a delimiter around code content in prompts.  The random suffix
    makes it infeasible for attacker-controlled content to replicate
    the boundary.

    Returns:
        A unique boundary marker string.
    """
    suffix = uuid.uuid4().hex[:8]
    return f"{_BOUNDARY_PREFIX}_{suffix}"

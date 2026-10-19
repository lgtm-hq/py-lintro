"""The Findings heading of the sticky board, written and read in one place.

The sticky no longer embeds its state (#2154), so the heading is the one
durable statement of which round the board was rendered for and at which
head. The already-reviewed guard (#2338) reads it back as publication
evidence; keeping the prefix and the parser beside each other keeps the two
from drifting.
"""

from __future__ import annotations

import re

__all__ = ["FINDINGS_HEADING_PREFIX", "parse_findings_heading"]

#: Start of the Findings heading; the round number follows it directly.
FINDINGS_HEADING_PREFIX = "### Findings · Round "

_FINDINGS_HEADING_RE = re.compile(
    r"^" + re.escape(FINDINGS_HEADING_PREFIX) + r"(\d+)(?: · `([0-9a-f]+)`)?",
    re.MULTILINE,
)


def parse_findings_heading(*, body: str) -> tuple[int, str] | None:
    """Read the round and head the sticky's Findings heading names.

    The sticky no longer embeds its state (#2154), so this heading is the
    one durable statement of which round the board was rendered for. The
    already-reviewed guard (#2338) reads it as publication evidence.

    Args:
        body: The sticky comment body.

    Returns:
        ``(round, short_sha)`` from the heading, with an empty sha when the
        heading carries none, or ``None`` when the body has no heading.
    """
    match = _FINDINGS_HEADING_RE.search(body)
    if match is None:
        return None
    return int(match.group(1)), match.group(2) or ""

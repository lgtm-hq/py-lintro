"""Hidden markers and links tying a comment to the finding it carries (#1912).

The review-submission endpoint does not answer with the ids of the comments it
created, so a posted inline comment is recognized again by a marker in its
body. The link builder lives here too: more than one surface points at a
thread, and the address is the same wherever it is rendered.
"""

from __future__ import annotations

import re

__all__ = [
    "FINDING_MARKER_PREFIX",
    "FINDING_MARKER_SUFFIX",
    "finding_marker",
    "inline_comment_url",
    "parse_finding_marker",
]

#: Hidden marker tying an inline comment to the finding record that owns it.
FINDING_MARKER_PREFIX = "<!-- lintro-finding:"
FINDING_MARKER_SUFFIX = " -->"

_FINDING_MARKER_RE = re.compile(
    re.escape(FINDING_MARKER_PREFIX) + r"\s*([0-9a-f]+#\d+)\s*-->",
)

#: Keys are hashes and ordinals, never model text, but the value is still
#: interpolated into a comment body — keep it to the shape the writer emits.
_KEY_RE = re.compile(r"^[0-9a-f]+#\d+$")


def finding_marker(*, key: str) -> str:
    """Render the hidden marker identifying a finding's inline comment.

    Args:
        key: The finding record's ``fingerprint#ordinal`` identity key.

    Returns:
        The HTML comment marker, or an empty string when the key is not a
        well-formed identity key.
    """
    if not _KEY_RE.match(key):
        return ""
    return f"{FINDING_MARKER_PREFIX}{key}{FINDING_MARKER_SUFFIX}"


def parse_finding_marker(*, body: str) -> str:
    """Extract the finding key a comment body is marked with.

    Args:
        body: Inline comment body as returned by the API.

    Returns:
        The identity key, or an empty string when the body carries no marker
        (a human's reply, or a comment from another tool).
    """
    match = _FINDING_MARKER_RE.search(body)
    return match.group(1) if match else ""


def inline_comment_url(
    *,
    repo: str,
    pr_number: int | str | None,
    comment_id: int | None,
) -> str:
    """Build the browser URL of an inline review comment.

    Lives here rather than on the posting adapter because more than one
    surface links to a thread: the lifecycle banners point back at the
    original one, and the sticky comment's open-findings table points at each
    finding's live one.

    Args:
        repo: ``owner/name`` repository slug.
        pr_number: Pull request number, or ``None`` when it is unknown.
        comment_id: Review comment id, or ``None`` when it is unknown.

    Returns:
        The comment's anchor URL, or an empty string when any part of the
        address is missing — a pointer renders unlinked rather than as a dead
        link.
    """
    if comment_id is None or not repo or pr_number is None:
        return ""
    return f"https://github.com/{repo}/pull/{pr_number}#discussion_r{comment_id}"

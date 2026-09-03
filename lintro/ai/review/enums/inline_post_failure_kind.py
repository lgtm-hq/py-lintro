"""Classified causes for a rejected inline review POST (#2266)."""

from __future__ import annotations

import re
from enum import StrEnum, auto

#: Substring GitHub uses when it throttles content creation on a token.
_SECONDARY_RATE_LIMIT: str = "secondary rate limit"

#: Whole words a 422 validation error uses when a comment anchors outside
#: the diff. Bounded so "pipeline" or "disposition" in an unrelated message
#: never reads as a line-mapping problem.
_LINE_ANCHOR_RE: re.Pattern[str] = re.compile(r"\b(?:line|position)\b")


class InlinePostFailureKind(StrEnum):
    """Why GitHub refused to create this round's inline review comments.

    The sticky comment used to blame every rejection on findings that anchor
    outside the diff, because the post helper only reported a boolean and the
    wording had to guess (#2266). The kind is derived from what GitHub
    actually answered, so a throttled token is never reported as a
    line-mapping problem.

    Attributes:
        RATE_LIMITED: GitHub throttled content creation for this token
            (HTTP 403 or 429 carrying "secondary rate limit").
        LINE_MAPPING: A comment anchored to a line that is not in the diff.
        PERMISSION: The token may not post reviews on this pull request.
        OTHER: Any other rejection, including transport failures where GitHub
            never answered at all.
    """

    RATE_LIMITED = auto()
    LINE_MAPPING = auto()
    PERMISSION = auto()
    OTHER = auto()

    @classmethod
    def from_response(
        cls,
        *,
        status: int | None,
        message: str,
    ) -> InlinePostFailureKind:
        """Classify a rejected review POST from its status and message.

        Args:
            status: HTTP status GitHub answered with, or ``None`` when the
                request never reached it.
            message: Error text GitHub returned, empty when unavailable.

        Returns:
            The matching kind. Unrecognized answers fall back to
            :attr:`OTHER` rather than to a specific cause the code never saw.
        """
        text = message.lower()
        # GitHub answers a secondary rate limit with 403 on the reviews
        # endpoint and 429 on others; both carry the same sentence.
        if status in (403, 429) and _SECONDARY_RATE_LIMIT in text:
            return cls.RATE_LIMITED
        if status == 422 and _LINE_ANCHOR_RE.search(text):
            return cls.LINE_MAPPING
        if status in (401, 403):
            return cls.PERMISSION
        return cls.OTHER

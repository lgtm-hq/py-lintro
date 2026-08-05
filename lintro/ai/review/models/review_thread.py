"""GraphQL review-thread reference for the addressed lifecycle (#1912)."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["ReviewThread"]


@dataclass(frozen=True, slots=True)
class ReviewThread:
    """A PR review thread as addressed by the GraphQL API.

    Attributes:
        node_id: GraphQL node id, the only handle ``resolveReviewThread``
            accepts. REST comment ids cannot be used for the mutation.
        is_resolved: Whether the thread is already resolved. An already
            resolved thread is skipped rather than re-resolved, which keeps a
            re-run from writing a second resolution event onto the timeline.
    """

    node_id: str
    is_resolved: bool = False

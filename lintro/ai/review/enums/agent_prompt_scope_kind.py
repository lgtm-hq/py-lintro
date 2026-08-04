"""Scope kinds for generated AI-agent remediation prompts."""

from __future__ import annotations

from enum import StrEnum, auto

__all__ = ["AgentPromptScopeKind"]


class AgentPromptScopeKind(StrEnum):
    """Which finding set an agent prompt covers.

    The kind drives the scope sentence restated on the prompt's first line and
    the panel title, so a reader can never confuse the sticky comment's
    cumulative prompt with a single review round's prompt.

    Attributes:
        ALL_OPEN: Every finding still open on the PR across all rounds. Used by
            the sticky status comment.
        THIS_REVIEW: Only the findings posted in the current review round. Used
            by the per-review comment body.
        SINGLE_FINDING: Exactly one finding. Used by inline review comments.
    """

    ALL_OPEN = auto()
    THIS_REVIEW = auto()
    SINGLE_FINDING = auto()

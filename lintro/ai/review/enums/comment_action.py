"""What a lifecycle decision does to a pull-request comment (#2305)."""

from __future__ import annotations

from enum import StrEnum, auto

__all__ = ["CommentAction"]


class CommentAction(StrEnum):
    """The three things a review can do with a comment of a given kind.

    Attributes:
        CREATE: No comment of this kind exists yet, so one is posted.
        UPDATE: The existing comment is edited in place, which is what keeps
            a sticky comment sticky.
        SUPERSEDE: The existing comment cannot be edited — GitHub only lets
            the creating actor PATCH, and #2050 changed the poster — so a
            replacement is posted and the old comment deleted.
    """

    CREATE = auto()
    UPDATE = auto()
    SUPERSEDE = auto()

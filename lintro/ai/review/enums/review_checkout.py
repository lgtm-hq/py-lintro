"""Which side of the reviewed range the working tree holds."""

from __future__ import annotations

from enum import StrEnum, auto


class ReviewCheckout(StrEnum):
    """What the files on disk show relative to the diff under review (#2685).

    The git-native review prompt tells the agent what a file read from disk
    means, and that differs per review mode: a CI pull-request review runs
    on a checkout of the *base* ref, a branch review runs on the branch
    itself, an uncommitted review runs on the change in the working tree.

    Attributes:
        BASE: Disk holds the base ref; every changed file reads pre-change.
        HEAD: Disk holds the head commit; changed files read post-change.
        WORKTREE: Disk holds the uncommitted change itself; post-change.
        UNKNOWN: Not determined (the checkout is neither end of the range);
            the prompt tells the agent to check.
        NONE: There is no tree the agent may read (#2733): a ``--pr`` review
            with no local repository, or one whose head could not be checked
            out. Every provider call goes out without tools.
    """

    BASE = auto()
    HEAD = auto()
    WORKTREE = auto()
    UNKNOWN = auto()
    NONE = auto()

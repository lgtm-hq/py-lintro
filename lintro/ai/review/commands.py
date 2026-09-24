"""Parse ``@lintro review`` requests posted as pull-request comments (#2627).

A user with write access asks for a review round by commenting on the pull
request. This module turns the comment text into a :class:`ReviewCommand` and
nothing else: it never talks to GitHub, never decides who may ask, and never
runs a review. The CI gate (``scripts/ci/resolve_review_request.py``) loads it
by file path, before lintro is installed, so it imports only the standard
library; that is also why :class:`ReviewRequestMode` lives here rather than in
``lintro.ai.review.enums``.

Grammar (first line of the comment only, case-insensitive command word):

* ``@lintro review``: a full review (carried coverage discarded).
* ``@lintro review delta``: the change since the last round.
* ``@lintro review <path> [<path> ...]``: only files under those path
  prefixes. Prefixes only in v1: glob characters, ``..`` segments,
  absolute paths, a leading ``-`` and anything outside ``[A-Za-z0-9._/-]``
  are rejected.

A comment that does not start with the command is not a request at all
(``None``). A comment that starts with it but is malformed is a
:attr:`ReviewRequestMode.USAGE` command carrying the reason, so the caller can
answer with the usage text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum, auto
from typing import Final

__all__ = [
    "COMMAND",
    "MAX_PATHS",
    "ReviewCommand",
    "ReviewRequestMode",
    "USAGE_TEXT",
    "parse_review_command",
]

#: The command word, matched case-insensitively at the very start of the
#: comment (the workflow's ``startsWith`` pre-filter matches the same way).
COMMAND: Final[str] = "@lintro review"

#: Most path prefixes one request may name; a longer list is a usage error.
MAX_PATHS: Final[int] = 20

#: Longest accepted path prefix.
_MAX_PATH_LENGTH: Final[int] = 200

#: Characters a path prefix may contain. No glob characters, no whitespace,
#: nothing a shell or a git pathspec would treat specially.
_PATH_RE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9._/-]+")

#: The reply for a malformed request. Constant text: nothing from the comment
#: is ever echoed back.
USAGE_TEXT: Final[str] = (
    "Usage (first line of a comment, by a user with write access):\n\n"
    "- `@lintro review`: full review of the whole diff\n"
    "- `@lintro review delta`: review the change since the last round\n"
    "- `@lintro review <path> [<path> ...]`: review only files under these "
    "path prefixes (no globs, no `..`, no leading `/`)"
)


class ReviewRequestMode(StrEnum):
    """What a ``@lintro review`` comment asked for.

    Attributes:
        FULL: Review the whole diff again, discarding carried coverage.
        DELTA: Review the change since the last recorded round.
        PATHS: Review only files under the requested path prefixes.
        USAGE: The comment started with the command but was malformed.
    """

    FULL = auto()
    DELTA = auto()
    PATHS = auto()
    USAGE = auto()


@dataclass(frozen=True)
class ReviewCommand:
    """A parsed review request.

    Attributes:
        mode: What was asked for.
        paths: Path prefixes, only for :attr:`ReviewRequestMode.PATHS`.
        problem: Why the request is malformed, only for
            :attr:`ReviewRequestMode.USAGE`. Never contains comment text.
    """

    mode: ReviewRequestMode
    paths: tuple[str, ...] = field(default=())
    problem: str = ""


def _usage(problem: str) -> ReviewCommand:
    """Return a usage command.

    Args:
        problem: Why the request is malformed.

    Returns:
        A :attr:`ReviewRequestMode.USAGE` command.
    """
    return ReviewCommand(mode=ReviewRequestMode.USAGE, problem=problem)


def _path_problem(path: str) -> str:
    """Return why a path prefix is refused, or an empty string.

    Args:
        path: One requested path prefix.

    Returns:
        The reason the prefix is refused; empty when it is acceptable.
    """
    if len(path) > _MAX_PATH_LENGTH:
        return "a path prefix is longer than 200 characters"
    if not _PATH_RE.fullmatch(path):
        return "a path prefix contains a character other than letters, digits, . _ / -"
    if path.startswith("/"):
        return "a path prefix is absolute"
    if path.startswith("-"):
        # Never exploitable (click takes the next token as --path's value),
        # but a prefix that reads like an option is refused on principle.
        return "a path prefix starts with `-`"
    if ".." in path.split("/"):
        return "a path prefix contains a `..` segment"
    return ""


def parse_review_command(body: str) -> ReviewCommand | None:
    """Parse a pull-request comment into a review request.

    Args:
        body: The full comment text.

    Returns:
        The request, a usage command for a malformed request, or ``None``
        when the comment is not a review request at all.
    """
    if body[: len(COMMAND)].casefold() != COMMAND:
        return None
    first_line = body.splitlines()[0]
    rest = first_line[len(COMMAND) :]
    if rest and not rest[0].isspace():
        # "@lintro reviewer" and the like are not this command.
        return None
    words = rest.split()
    if not words:
        return ReviewCommand(mode=ReviewRequestMode.FULL)
    if words[0].casefold() == "delta":
        if len(words) > 1:
            return _usage("`delta` takes no arguments")
        return ReviewCommand(mode=ReviewRequestMode.DELTA)
    if len(words) > MAX_PATHS:
        return _usage(f"at most {MAX_PATHS} path prefixes per request")
    for word in words:
        problem = _path_problem(word)
        if problem:
            return _usage(problem)
    return ReviewCommand(mode=ReviewRequestMode.PATHS, paths=tuple(words))

"""Rendering mode for an inline finding comment's fix slot."""

from __future__ import annotations

from enum import StrEnum, auto


class FixMode(StrEnum):
    """Which fix affordance an inline finding comment renders (#1911).

    The inline comment format has one conditional slot: either a committable
    GitHub ``suggestion`` block or a highlighted one-line fix description.

    Attributes:
        SUGGESTION: Mode A — a validated ``suggestion`` block the reviewer can
            commit in one click.
        DESCRIBED: Mode B — a ``**Fix:**`` one-liner, used whenever a
            committable suggestion is unavailable or would be rejected.
    """

    SUGGESTION = auto()
    DESCRIBED = auto()

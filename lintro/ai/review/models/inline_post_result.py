"""Outcome of one attempt to post this round's inline review comments."""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["InlinePostResult"]


@dataclass(frozen=True, slots=True)
class InlinePostResult:
    """What happened when the inline review batch was submitted.

    Attributes:
        ok: Whether GitHub accepted the review (also true when there was
            nothing to post).
        status: HTTP status GitHub answered with, or ``None`` when no request
            was made or the request never reached GitHub.
        message: Error text GitHub returned, empty on success.
        attempted_ids: Identity key of every finding carried by the batch, in
            posting order. Empty entries are findings whose record could not
            be paired, and are kept so the tuple stays positional.
    """

    ok: bool
    status: int | None = None
    message: str = ""
    attempted_ids: tuple[str, ...] = field(default_factory=tuple)

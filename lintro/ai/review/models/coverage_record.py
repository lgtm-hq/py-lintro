"""One file's coverage entry keyed by ``(path, hash)`` (#2154)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from lintro.ai.review.models._coerce import coerce_int

__all__ = ["CoverageRecord"]


@dataclass(frozen=True, slots=True)
class CoverageRecord:
    """A reviewed file at one normalized patch hash.

    The lookup key is ``(path, hash)``. ``reviewed_sha`` is metadata so a
    content-identical rebase does not drop coverage.

    Attributes:
        path: Repository-relative file path.
        patch_hash: Normalized ``+``/``-`` hash at review time.
        reviewed_sha: Head SHA when this entry was written (advisory).
        round: Round number that produced the entry.
        stopped_reason: Empty when the file finished; otherwise the
            mid-round stop that checkpointed this entry.
    """

    path: str
    patch_hash: str
    reviewed_sha: str = ""
    round: int = 1
    stopped_reason: str = ""

    @property
    def identity(self) -> tuple[str, str]:
        """Return the coverage lookup key."""
        return (self.path, self.patch_hash)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the record for the artifact envelope.

        Returns:
            JSON-serializable mapping.
        """
        payload: dict[str, Any] = {
            "path": self.path,
            "hash": self.patch_hash,
            "reviewed_sha": self.reviewed_sha,
            "round": self.round,
        }
        if self.stopped_reason:
            payload["stopped_reason"] = self.stopped_reason
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> CoverageRecord | None:
        """Parse a coverage record from untrusted JSON.

        Args:
            payload: Decoded mapping.

        Returns:
            The record, or ``None`` when required fields are missing.
            Callers drop invalid entries (fail toward more review).
        """
        path = str(payload.get("path", "")).strip()
        patch_hash = str(payload.get("hash", "")).strip()
        if not path or not patch_hash:
            return None
        return cls(
            path=path,
            patch_hash=patch_hash,
            reviewed_sha=str(payload.get("reviewed_sha", "")),
            round=coerce_int(payload.get("round"), default=1) or 1,
            stopped_reason=str(payload.get("stopped_reason", "")),
        )

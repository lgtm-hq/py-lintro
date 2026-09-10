"""Reviewer-proposed re-read request with a reason (#2154)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["FlaggedFile"]


@dataclass(frozen=True, slots=True)
class FlaggedFile:
    """A model-flagged file that should re-enter the review queue.

    Guards (applied by the classifier, not this record): allowlist to PR
    changed files; one-way covered→needs-review; capped per round; same
    ``(path, hash)`` flagged once.

    Attributes:
        path: Repository-relative path.
        reason: Why the reviewer asked for another look.
        patch_hash: Hash at flag time, when known.
    """

    path: str
    reason: str
    patch_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize the flag.

        Returns:
            JSON-serializable mapping.
        """
        payload: dict[str, Any] = {"path": self.path, "reason": self.reason}
        if self.patch_hash:
            payload["hash"] = self.patch_hash
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> FlaggedFile | None:
        """Parse a flag from untrusted JSON.

        Args:
            payload: Decoded mapping.

        Returns:
            The flag, or ``None`` when path or reason is empty.
        """
        path = str(payload.get("path", "")).strip()
        reason = str(payload.get("reason", "")).strip()
        if not path or not reason:
            return None
        return cls(
            path=path,
            reason=reason,
            patch_hash=str(payload.get("hash", "")).strip(),
        )

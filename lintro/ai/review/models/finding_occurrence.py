"""One location of a repeated finding pattern."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from lintro.ai.review.models._coerce import coerce_int

__all__ = ["FindingOccurrence", "parse_occurrences"]


@dataclass(frozen=True, slots=True)
class FindingOccurrence:
    """A single ``file:line`` location at which a finding pattern occurs.

    The same defect repeated across twenty call sites is one finding with
    twenty occurrences, not twenty findings (#1925): display collapses to one
    thread, prompts enumerate every location, and the matcher resolves the
    pattern only once every occurrence is gone.

    Attributes:
        file: Repository-relative file path.
        line: Line number within that file.
    """

    file: str
    line: int = 0

    @property
    def label(self) -> str:
        """Return the ``file:line`` label used in prompts and displays."""
        return f"{self.file}:{self.line}"

    def to_dict(self) -> dict[str, Any]:
        """Serialize the occurrence for the hidden state blob.

        Returns:
            JSON-serializable mapping with ``file`` and ``line`` keys.
        """
        return {"file": self.file, "line": self.line}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> FindingOccurrence | None:
        """Rebuild an occurrence from an untrusted mapping.

        Args:
            payload: Decoded mapping for one occurrence.

        Returns:
            The parsed occurrence, or ``None`` when it names no usable file.
            A non-string ``file`` is rejected rather than coerced: ``str()``
            would turn ``None`` into the path ``"None"`` and quietly invent a
            location the model never reported.
        """
        file = payload.get("file")
        if not isinstance(file, str) or not file.strip():
            return None
        return cls(file=file.strip(), line=coerce_int(payload.get("line")))


def parse_occurrences(value: Any) -> tuple[FindingOccurrence, ...]:
    """Parse an occurrence list from untrusted model or state-blob input.

    Args:
        value: Raw ``occurrences`` value.

    Returns:
        The parsed occurrences; empty when the value is not a list of usable
        mappings. Malformed entries are dropped rather than failing the run.
    """
    if not isinstance(value, list):
        return ()
    parsed = (
        FindingOccurrence.from_dict(item) for item in value if isinstance(item, dict)
    )
    return tuple(occurrence for occurrence in parsed if occurrence is not None)

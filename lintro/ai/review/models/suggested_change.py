"""Structured replacement hunk carried by a review finding."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from lintro.ai.review.models._coerce import coerce_int

__all__ = ["SuggestedChange", "parse_suggested_change"]


@dataclass(frozen=True, slots=True)
class SuggestedChange:
    """An exact replacement for a contiguous run of lines (#1911).

    A GitHub ``suggestion`` block is only committable when it replaces
    *exactly* the lines the review comment is anchored to, so the fix has to
    name its own line range rather than leaving the renderer to guess one.
    ``suggested_code`` (the older, unranged field) is treated as a change over
    the finding's own single line.

    Attributes:
        start_line: First replaced line, 1-based and inclusive.
        end_line: Last replaced line, 1-based and inclusive.
        replacement: Full replacement text for those lines, without a trailing
            newline. Every replaced line must be accounted for — a partial
            replacement would silently delete the lines it omits.
    """

    start_line: int
    end_line: int
    replacement: str

    @property
    def line_span(self) -> range:
        """Return the inclusive line range this change replaces."""
        return range(self.start_line, self.end_line + 1)

    @property
    def is_multiline(self) -> bool:
        """Return True when the change spans more than one source line."""
        return self.end_line > self.start_line

    def to_dict(self) -> dict[str, Any]:
        """Serialize the change for the review output payload.

        Returns:
            JSON-serializable mapping with ``lines`` and ``replacement`` keys.
        """
        return {
            "lines": [self.start_line, self.end_line],
            "replacement": self.replacement,
        }


def parse_suggested_change(value: Any) -> SuggestedChange | None:
    """Parse a ``suggested_change`` payload from untrusted model output.

    The field is optional everywhere: a malformed payload degrades to ``None``
    (the finding then renders without a committable suggestion) rather than
    failing the run.

    Args:
        value: Raw ``suggested_change`` value, expected to be a mapping with a
            two-element ``lines`` list and a string ``replacement``.

    Returns:
        The parsed change, or ``None`` when the payload is unusable. Range
        sanity (positive, ordered, matching the anchor) is *not* checked here —
        that is the renderer's validity gate, which records why a suggestion
        was rejected.
    """
    if not isinstance(value, dict):
        return None
    replacement = value.get("replacement")
    if not isinstance(replacement, str):
        return None
    lines = value.get("lines")
    if not isinstance(lines, list) or len(lines) != 2:
        return None
    start_line = coerce_int(lines[0])
    end_line = coerce_int(lines[1])
    return SuggestedChange(
        start_line=start_line,
        end_line=end_line,
        replacement=replacement,
    )

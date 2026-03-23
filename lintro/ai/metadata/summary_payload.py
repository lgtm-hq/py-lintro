"""Serialized summary payload for JSON output."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class AISummaryPayload:
    """Serialized summary payload for JSON output."""

    overview: str = ""
    key_patterns: list[str] = field(default_factory=list)
    priority_actions: list[str] = field(default_factory=list)
    triage_suggestions: list[str] = field(default_factory=list)
    estimated_effort: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_estimate: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return asdict(self)

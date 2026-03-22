"""Serialized fix suggestion payload for JSON output."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from lintro.ai.enums import ConfidenceLevel, RiskLevel


@dataclass
class AIFixSuggestionPayload:
    """Serialized fix suggestion payload for JSON output."""

    file: str = ""
    line: int = 0
    code: str = ""
    tool_name: str = ""
    original_code: str = ""
    suggested_code: str = ""
    explanation: str = ""
    confidence: ConfidenceLevel | str = ""
    risk_level: RiskLevel | str = ""
    diff: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_estimate: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return asdict(self)

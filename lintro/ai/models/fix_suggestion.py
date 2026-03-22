"""AI-generated fix suggestion dataclass."""

from __future__ import annotations

from dataclasses import dataclass

from lintro.ai.enums import ConfidenceLevel, RiskLevel


@dataclass
class AIFixSuggestion:
    """AI-generated fix suggestion with a unified diff.

    Generated for issues that native tools cannot auto-fix.

    Attributes:
        file: Path to the file being fixed.
        line: Line number of the original issue.
        code: Error code of the issue being fixed.
        tool_name: Name of the tool that reported this issue.
        original_code: The original code snippet.
        suggested_code: The AI-suggested replacement.
        diff: Unified diff string showing the change.
        explanation: Brief explanation of what was changed and why.
        confidence: Confidence level.
        risk_level: AI-reported risk classification. Empty string
            if not classified.
        input_tokens: Tokens consumed for input in the API call.
        output_tokens: Tokens generated for output in the API call.
        cost_estimate: Estimated cost in USD for the API call.
    """

    file: str = ""
    line: int = 0
    code: str = ""
    tool_name: str = ""
    original_code: str = ""
    suggested_code: str = ""
    diff: str = ""
    explanation: str = ""
    confidence: ConfidenceLevel | str = ConfidenceLevel.MEDIUM
    risk_level: RiskLevel | str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_estimate: float = 0.0

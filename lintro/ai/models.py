"""Data models for AI-powered issue intelligence.

Defines the structures used to represent AI summaries, explanations
grouped by error code, and AI-generated fix suggestions with diffs.
"""

from __future__ import annotations

from dataclasses import dataclass, field


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
        confidence: Confidence level ("high", "medium", "low").
        risk_level: AI-reported risk classification ("safe-style" or
            "behavioral-risk"). Empty string if not classified.
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
    confidence: str = "medium"
    risk_level: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_estimate: float = 0.0


@dataclass
class AISummary:
    """AI-generated high-level summary of all issues across tools.

    Produced by a single API call that analyzes the full issue digest
    and returns structured, actionable insights.

    Attributes:
        overview: 2-3 sentence high-level assessment.
        key_patterns: Recurring issue patterns identified across the codebase.
        priority_actions: Ordered list of recommended actions, most impactful first.
        triage_suggestions: Issues that are likely intentional/idiomatic with
        suppression advice.
        estimated_effort: Rough effort estimate to address all issues.
        input_tokens: Tokens consumed for input.
        output_tokens: Tokens generated for output.
        cost_estimate: Estimated cost in USD.
    """

    overview: str = ""
    key_patterns: list[str] = field(default_factory=list)
    priority_actions: list[str] = field(default_factory=list)
    triage_suggestions: list[str] = field(default_factory=list)
    estimated_effort: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_estimate: float = 0.0

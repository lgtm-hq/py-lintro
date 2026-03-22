"""AI summary dataclass."""

from __future__ import annotations

from dataclasses import dataclass, field


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

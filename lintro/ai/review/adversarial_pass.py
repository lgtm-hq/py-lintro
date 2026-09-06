"""Depth-3 adversarial sweep for a review chunk.

The main review pass optimises for precision; at depth 3 the same chunk gets a
second look that is told what was already reported and asked what it missed
(issue #2301). The sweep contributes findings and usage only -- it never edits
the main pass's answers -- so its partial carries an empty summary and checklist
and the caller merges its findings into the chunk's.

A malformed sweep answer is not an error: the chunk keeps the main pass's result
and the sweep contributes usage alone.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from loguru import logger

from lintro.ai.invoke import call_ai
from lintro.ai.json_response import strip_json_fences
from lintro.ai.prompts.review import (
    REVIEW_ADVERSARIAL_SWEEP_TEMPLATE,
    REVIEW_SYSTEM,
)
from lintro.ai.review.finding_parser import parse_findings
from lintro.ai.review.merge import ChunkReviewPartial
from lintro.ai.review.prompt_redaction import redact_prompt_text
from lintro.ai.sanitize import make_boundary_marker

if TYPE_CHECKING:
    from lintro.ai.budget import CostBudget
    from lintro.ai.config import AIConfig
    from lintro.ai.providers.base import BaseAIProvider
    from lintro.ai.review.models.review_chunk import ReviewChunk
    from lintro.ai.review.models.review_finding import ReviewFinding

__all__ = ["run_adversarial_pass"]


async def run_adversarial_pass(
    *,
    chunk: ReviewChunk,
    provider: BaseAIProvider,
    ai_config: AIConfig,
    prior_findings: tuple[ReviewFinding, ...],
    budget: CostBudget,
    repo_root: str = "",
    use_one_shot: bool = False,
) -> ChunkReviewPartial:
    """Run depth-3 adversarial sweep for missed findings.

    Args:
        chunk: The chunk being reviewed.
        provider: Configured AI provider instance.
        ai_config: AI configuration for retries, budget, and fallbacks.
        prior_findings: Findings already reported for this chunk.
        budget: Session cost budget tracker.
        repo_root: Absolute path to the repository under review.
        use_one_shot: When True, avoid durable provider sessions.

    Returns:
        A partial carrying any additional findings and usage.
    """
    prior_json = json.dumps(
        [
            {
                "severity": finding.severity,
                "file": finding.file,
                "line": finding.line,
                "title": finding.title,
            }
            for finding in prior_findings
        ],
    )
    prompt = REVIEW_ADVERSARIAL_SWEEP_TEMPLATE.format(
        prior_findings_json=prior_json,
        boundary=make_boundary_marker(),
        diff=redact_prompt_text(text=chunk.diff, source="diff"),
    )
    budget.check()
    response = await call_ai(
        provider=provider,
        ai_config=ai_config,
        system_prompt=REVIEW_SYSTEM,
        user_prompt=prompt,
        budget=budget,
        repo_root=repo_root or None,
        use_one_shot=use_one_shot,
    )
    try:
        payload = json.loads(strip_json_fences(content=response.content))
    except (json.JSONDecodeError, ValueError):
        logger.warning("Failed to parse adversarial sweep response")
        return ChunkReviewPartial(
            summary="",
            checklist=(),
            findings=(),
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cost_estimate=response.cost_estimate,
        )

    if not isinstance(payload, dict):
        logger.warning("Adversarial sweep payload was not an object")
        return ChunkReviewPartial(
            summary="",
            checklist=(),
            findings=(),
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cost_estimate=response.cost_estimate,
        )

    findings_raw = payload.get("findings", [])
    findings = parse_findings(raw_findings=findings_raw)
    return ChunkReviewPartial(
        summary="",
        checklist=(),
        findings=findings,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        cost_estimate=response.cost_estimate,
    )

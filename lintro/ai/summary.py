"""AI summary service for generating high-level actionable insights.

Takes all issues across all tools and produces a single concise summary
with pattern analysis and prioritized recommendations. Uses a single
API call for cost efficiency.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from lintro.ai.models import AISummary
from lintro.ai.paths import resolve_workspace_root, to_provider_path
from lintro.ai.prompts import (
    POST_FIX_SUMMARY_PROMPT_TEMPLATE,
    SUMMARY_PROMPT_TEMPLATE,
    SUMMARY_SYSTEM,
)
from lintro.ai.retry import with_retry

if TYPE_CHECKING:
    from lintro.ai.providers.base import AIResponse, BaseAIProvider
    from lintro.models.core.tool_result import ToolResult


def _build_issues_digest(
    results: Sequence[ToolResult],
    *,
    workspace_root: Path | None = None,
) -> str:
    """Build a compact textual digest of all issues across tools.

    Groups by tool and error code, shows counts and sample locations.
    Designed to fit within a single prompt without being too verbose.

    Args:
        results: Tool results containing parsed issues.
        workspace_root: Optional root used for provider-safe path redaction.

    Returns:
        Formatted digest string for inclusion in the prompt.
    """
    root = workspace_root or resolve_workspace_root()
    lines: list[str] = []
    for result in results:
        if not result.issues or result.skipped:
            continue
        issues = list(result.issues)
        if not issues:
            continue

        # Group by code within this tool
        by_code: dict[str, list[object]] = defaultdict(list)
        for issue in issues:
            code = getattr(issue, "code", None) or "unknown"
            by_code[code].append(issue)

        lines.append(f"\n## {result.name} ({len(issues)} issues)")
        for code, code_issues in sorted(
            by_code.items(),
            key=lambda x: -len(x[1]),
        ):
            sample_locs = []
            for iss in code_issues[:3]:
                loc = to_provider_path(getattr(iss, "file", ""), root)
                line_no = getattr(iss, "line", None)
                if line_no:
                    loc += f":{line_no}"
                sample_locs.append(loc)
            more = f" (+{len(code_issues) - 3} more)" if len(code_issues) > 3 else ""
            first_msg = getattr(code_issues[0], "message", "")
            lines.append(
                f"  [{code}] x{len(code_issues)}: "
                f"{first_msg}"
                f"\n    e.g. {', '.join(sample_locs)}{more}",
            )

    return "\n".join(lines)


def _parse_summary_response(
    content: str,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost_estimate: float = 0.0,
) -> AISummary:
    """Parse the AI summary response into an AISummary.

    Falls back gracefully if JSON parsing fails.

    Args:
        content: Raw AI response content.
        input_tokens: Tokens consumed for input.
        output_tokens: Tokens generated for output.
        cost_estimate: Estimated cost in USD.

    Returns:
        Parsed AISummary.
    """
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        logger.debug("Failed to parse AI summary response as JSON")
        return AISummary(
            overview=content[:500] if content else "Summary unavailable",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_estimate=cost_estimate,
        )

    return AISummary(
        overview=data.get("overview", ""),
        key_patterns=data.get("key_patterns", []),
        priority_actions=data.get("priority_actions", []),
        triage_suggestions=data.get("triage_suggestions", []),
        estimated_effort=data.get("estimated_effort", ""),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_estimate=cost_estimate,
    )


def generate_summary(
    results: Sequence[ToolResult],
    provider: BaseAIProvider,
    *,
    max_tokens: int = 2048,
    workspace_root: Path | None = None,
) -> AISummary | None:
    """Generate a high-level AI summary of all issues.

    Makes a single API call with a digest of all issues and returns
    structured actionable insights.

    Args:
        results: Tool results containing parsed issues.
        provider: AI provider instance.
        max_tokens: Maximum tokens for the response.
        workspace_root: Optional root used for provider-safe path redaction.

    Returns:
        AISummary, or None if generation fails or there are no issues.
    """
    digest = _build_issues_digest(results, workspace_root=workspace_root)
    if not digest.strip():
        return None

    total_issues = sum(
        len(list(r.issues)) for r in results if r.issues and not r.skipped
    )
    tool_count = sum(1 for r in results if r.issues and not r.skipped)

    prompt = SUMMARY_PROMPT_TEMPLATE.format(
        total_issues=total_issues,
        tool_count=tool_count,
        issues_digest=digest,
    )

    @with_retry(max_retries=2)
    def _call() -> AIResponse:
        return provider.complete(
            prompt,
            system=SUMMARY_SYSTEM,
            max_tokens=max_tokens,
        )

    try:
        response = _call()
        return _parse_summary_response(
            response.content,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cost_estimate=response.cost_estimate,
        )
    except Exception:
        logger.debug("AI summary generation failed", exc_info=True)
        return None


def generate_post_fix_summary(
    *,
    applied: int,
    rejected: int,
    remaining_results: Sequence[ToolResult],
    provider: BaseAIProvider,
    max_tokens: int = 1024,
    workspace_root: Path | None = None,
) -> AISummary | None:
    """Generate a summary for the post-fix context.

    Contextualizes what was fixed and what remains, providing
    actionable next steps for remaining issues.

    Args:
        applied: Number of fixes applied.
        rejected: Number of fixes rejected.
        remaining_results: Tool results with remaining issues.
        provider: AI provider instance.
        max_tokens: Maximum tokens for the response.
        workspace_root: Optional root used for provider-safe path redaction.

    Returns:
        AISummary, or None if generation fails.
    """
    remaining_count = sum(
        len(list(r.issues)) for r in remaining_results if r.issues and not r.skipped
    )

    digest = _build_issues_digest(
        remaining_results,
        workspace_root=workspace_root,
    )
    if not digest.strip() and remaining_count == 0:
        # All issues resolved — no summary needed
        return None

    prompt = POST_FIX_SUMMARY_PROMPT_TEMPLATE.format(
        applied=applied,
        rejected=rejected,
        remaining=remaining_count,
        issues_digest=digest or "No remaining issues.",
    )

    @with_retry(max_retries=2)
    def _call() -> AIResponse:
        return provider.complete(
            prompt,
            system=SUMMARY_SYSTEM,
            max_tokens=max_tokens,
        )

    try:
        response = _call()
        return _parse_summary_response(
            response.content,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cost_estimate=response.cost_estimate,
        )
    except Exception:
        logger.debug("AI post-fix summary generation failed", exc_info=True)
        return None

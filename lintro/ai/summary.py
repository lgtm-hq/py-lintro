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
from lintro.ai.secrets import redact_secrets
from lintro.ai.token_budget import estimate_tokens

if TYPE_CHECKING:
    from lintro.ai.providers.base import AIResponse, BaseAIProvider
    from lintro.models.core.tool_result import ToolResult

# -- Type helpers --------------------------------------------------------------


def _ensure_str_list(value: object) -> list[str]:
    """Coerce an AI response value to a list of strings.

    Handles the case where the AI returns a plain string instead of
    a list, or a list containing non-string items.
    """
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    if isinstance(value, str):
        return [value]
    return []


def _build_issues_digest(
    results: Sequence[ToolResult],
    *,
    workspace_root: Path | None = None,
    max_tokens: int = 8000,
) -> str:
    """Build a compact textual digest of all issues across tools.

    Groups by tool and error code, shows counts and sample locations.
    Tracks token budget so the digest stays within *max_tokens*; when
    the budget is nearly exhausted the remaining tools/codes are
    summarised in a single truncation note.

    Args:
        results: Tool results containing parsed issues.
        workspace_root: Optional root used for provider-safe path redaction.
        max_tokens: Soft token budget for the entire digest (default 8000).

    Returns:
        Formatted digest string for inclusion in the prompt.
    """
    root = workspace_root or resolve_workspace_root()
    lines: list[str] = []
    used_tokens = 0
    truncated = False

    # Pre-compute per-tool issue lists so we can report omitted counts.
    tool_entries: list[tuple[str, list[object]]] = []
    for result in results:
        if not result.issues or result.skipped:
            continue
        issues: list[object] = list(result.issues)
        if issues:
            tool_entries.append((result.name, issues))

    omitted_issues = 0
    omitted_tools = 0

    for idx, (tool_name, issues) in enumerate(tool_entries):
        # Group by code within this tool
        by_code: dict[str, list[object]] = defaultdict(list)
        for issue in issues:
            code = getattr(issue, "code", None) or "unknown"
            by_code[code].append(issue)

        header = f"\n## {tool_name} ({len(issues)} issues)"
        header_tokens = estimate_tokens(header)
        if used_tokens + header_tokens > max_tokens:
            # Budget exhausted — count this tool and all remaining.
            omitted_issues += sum(len(iss) for _, iss in tool_entries[idx:])
            omitted_tools += len(tool_entries) - idx
            truncated = True
            break

        lines.append(header)
        used_tokens += header_tokens

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
            first_msg = redact_secrets(getattr(code_issues[0], "message", ""))
            entry = (
                f"  [{code}] x{len(code_issues)}: "
                f"{first_msg}"
                f"\n    e.g. {', '.join(sample_locs)}{more}"
            )
            entry_tokens = estimate_tokens(entry)
            if used_tokens + entry_tokens > max_tokens:
                # Count remaining codes in this tool + remaining tools.
                remaining_in_tool = sum(
                    len(ci)
                    for c, ci in by_code.items()
                    if c >= code  # rough: current + later sorted codes
                )
                omitted_issues += remaining_in_tool + sum(
                    len(iss) for _, iss in tool_entries[idx + 1 :]
                )
                omitted_tools += len(tool_entries) - idx - 1
                truncated = True
                break

            lines.append(entry)
            used_tokens += entry_tokens

        if truncated:
            break

    if truncated:
        note = f"\n(truncated — {omitted_issues} more issues"
        if omitted_tools:
            note += f" across {omitted_tools} tool{'s' if omitted_tools != 1 else ''}"
        note += ")"
        lines.append(note)

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

    if not isinstance(data, dict):
        logger.debug("AI summary response is not a JSON object")
        return AISummary(
            overview=str(data)[:500] if data else "Summary unavailable",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_estimate=cost_estimate,
        )

    return AISummary(
        overview=data.get("overview", ""),
        key_patterns=_ensure_str_list(data.get("key_patterns", [])),
        priority_actions=_ensure_str_list(data.get("priority_actions", [])),
        triage_suggestions=_ensure_str_list(data.get("triage_suggestions", [])),
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
    timeout: float = 60.0,
    max_retries: int = 2,
    base_delay: float | None = None,
    max_delay: float | None = None,
    backoff_factor: float | None = None,
) -> AISummary | None:
    """Generate a high-level AI summary of all issues.

    Makes a single API call with a digest of all issues and returns
    structured actionable insights.

    Args:
        results: Tool results containing parsed issues.
        provider: AI provider instance.
        max_tokens: Maximum tokens for the response.
        workspace_root: Optional root used for provider-safe path redaction.
        timeout: Request timeout in seconds per API call.
        max_retries: Maximum retry attempts for transient API failures.
        base_delay: Initial retry delay in seconds (None = use default).
        max_delay: Maximum retry delay in seconds (None = use default).
        backoff_factor: Retry backoff multiplier (None = use default).

    Returns:
        AISummary, or None if generation fails or there are no issues.
    """
    digest = _build_issues_digest(
        results,
        workspace_root=workspace_root,
        max_tokens=8000,
    )
    if not digest.strip():
        return None

    total_issues = sum(
        r.issues_count for r in results if r.issues_count and not r.skipped
    )
    tool_count = sum(1 for r in results if r.issues_count and not r.skipped)

    prompt = SUMMARY_PROMPT_TEMPLATE.format(
        total_issues=total_issues,
        tool_count=tool_count,
        issues_digest=digest,
    )

    @with_retry(
        max_retries=max_retries,
        base_delay=base_delay if base_delay is not None else 1.0,
        max_delay=max_delay if max_delay is not None else 30.0,
        backoff_factor=backoff_factor if backoff_factor is not None else 2.0,
    )
    def _call() -> AIResponse:
        return provider.complete(
            prompt,
            system=SUMMARY_SYSTEM,
            max_tokens=max_tokens,
            timeout=timeout,
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
    timeout: float = 60.0,
    max_retries: int = 2,
    base_delay: float | None = None,
    max_delay: float | None = None,
    backoff_factor: float | None = None,
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
        timeout: Request timeout in seconds per API call.
        max_retries: Maximum retry attempts for transient API failures.
        base_delay: Initial retry delay in seconds (None = use default).
        max_delay: Maximum retry delay in seconds (None = use default).
        backoff_factor: Retry backoff multiplier (None = use default).

    Returns:
        AISummary, or None if generation fails.
    """
    remaining_count = sum(
        len(list(r.issues)) for r in remaining_results if r.issues and not r.skipped
    )

    digest = _build_issues_digest(
        remaining_results,
        workspace_root=workspace_root,
        max_tokens=6000,
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

    @with_retry(
        max_retries=max_retries,
        base_delay=base_delay if base_delay is not None else 1.0,
        max_delay=max_delay if max_delay is not None else 30.0,
        backoff_factor=backoff_factor if backoff_factor is not None else 2.0,
    )
    def _call() -> AIResponse:
        return provider.complete(
            prompt,
            system=SUMMARY_SYSTEM,
            max_tokens=max_tokens,
            timeout=timeout,
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

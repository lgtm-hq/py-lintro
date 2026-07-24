"""Sticky-comment assembly, state, and size capping for GitHub reviews."""

from __future__ import annotations

import json
from typing import Any

from lintro.ai.review.enums.checklist_display import ChecklistDisplay
from lintro.ai.review.github_constants import (
    _CHECKLIST_APPENDIX_RE,
    _FINDING_BLOCK_START_RE,
    _FINDINGS_SECTION_RE,
    _FOOTER,
    _PREVIOUS_RUNS_RE,
    _RUN_MECHANICS_RE,
    _TRUNCATION_MARGIN,
    MAX_COMMENT_CHARS,
    MAX_STORED_RUNS,
    STATE_MARKER_PREFIX,
    STATE_MARKER_SUFFIX,
    STATE_VERSION,
    STICKY_MARKER,
)
from lintro.ai.review.github_render import (
    _fmt_cost,
    _fmt_tokens,
    _format_findings_section,
    _severity_counts,
    format_review_summary,
    format_run_mechanics,
    sanitize_comment_text,
)
from lintro.ai.review.models.review_finding import Severity
from lintro.ai.review.models.review_result import ReviewResult


def build_sticky_comment(
    *,
    result: ReviewResult,
    prior_runs: list[dict[str, Any]] | None = None,
    checklist_display: ChecklistDisplay = ChecklistDisplay.OFF,
    question_map: dict[int, str] | None = None,
    diff_lines: dict[str, set[int]] | None = None,
) -> str:
    """Compose the full sticky PR comment body, including cumulative telemetry.

    Non-diff-mappable ("fallback") findings — whose only surface is this sticky
    comment — render first in the findings section. When the assembled body
    exceeds ``MAX_COMMENT_CHARS`` the findings section is re-rendered against a
    character budget so overflow is dropped explicitly (a visible marker names
    the count) and only ever falls on findings that also post inline.

    Args:
        result: Current review result.
        prior_runs: Run records recovered from the previous sticky comment's
            state block. ``None`` for the first run on a PR.
        checklist_display: Structured checklist visibility mode.
        question_map: Prompt id to question text for the checklist appendix.
        diff_lines: Diff line map used to order fallback findings first and to
            decide which findings are safe to truncate. ``None`` treats all
            findings as fallback.

    Returns:
        Complete Markdown body carrying the hidden marker and state block.
    """
    prior = list(prior_runs or [])
    current = _run_record(result=result)
    all_runs = [*prior, current][-MAX_STORED_RUNS:]

    def assemble(*, findings_char_budget: int | None) -> str:
        sections = [STICKY_MARKER, _format_cumulative_header(runs=all_runs)]
        sections.append(
            format_review_summary(
                result=result,
                checklist_display=checklist_display,
                question_map=question_map,
                diff_lines=diff_lines,
                findings_char_budget=findings_char_budget,
            ),
        )
        sections.append(
            "<details><summary>⚙️ Run mechanics (this run)</summary>\n\n"
            + format_run_mechanics(metadata=result.metadata)
            + "\n\n</details>",
        )
        if prior:
            sections.append(_format_previous_runs(runs=prior))
        sections.append(_FOOTER)
        return "\n\n".join(sections)

    body = assemble(findings_char_budget=None)
    if len(body) > MAX_COMMENT_CHARS:
        # Isolate the findings section's contribution so the remaining budget
        # can be handed back to it explicitly, keeping fallback findings intact.
        findings_len = len(
            "\n".join(
                _format_findings_section(
                    findings=result.findings,
                    checklist_display=checklist_display,
                    question_map=question_map or {},
                    diff_lines=diff_lines,
                ),
            ),
        )
        overhead = len(body) - findings_len
        findings_budget = max(MAX_COMMENT_CHARS - overhead - _TRUNCATION_MARGIN, 0)
        body = assemble(findings_char_budget=findings_budget)

    body = _cap_body(body=body)
    return body + _render_state_block(runs=all_runs)


def _render_state_block(*, runs: list[dict[str, Any]]) -> str:
    """Render the hidden machine-readable run-state block."""
    return (
        f"\n\n{STATE_MARKER_PREFIX} "
        + json.dumps({"version": STATE_VERSION, "runs": runs})
        + f" {STATE_MARKER_SUFFIX}"
    )


def _run_record(*, result: ReviewResult) -> dict[str, Any]:
    """Build a machine-readable run record from a review result."""
    metadata = result.metadata
    counts = _severity_counts(findings=result.findings)
    usage = metadata.token_usage
    return {
        "timestamp": metadata.timestamp,
        "model": metadata.model,
        "provider": metadata.provider,
        "prompt": int(usage.get("prompt", 0)),
        "completion": int(usage.get("completion", 0)),
        "total": int(usage.get("total", 0)),
        "cost": round(metadata.cost_estimate_usd, 6),
        "estimated": bool(metadata.token_usage_estimated),
        "depth": metadata.depth,
        "duration": round(metadata.duration_seconds, 2),
        "p1": counts[Severity.P1],
        "p2": counts[Severity.P2],
        "p3": counts[Severity.P3],
        "partial": bool(metadata.partial),
        "chunks_reviewed": metadata.chunks_reviewed,
        "chunks_total": metadata.chunks_total,
    }


def _format_cumulative_header(*, runs: list[dict[str, Any]]) -> str:
    """Render the always-visible cumulative telemetry header for the PR."""
    total_tokens = sum(int(run.get("total", 0)) for run in runs)
    total_cost = sum(float(run.get("cost", 0.0)) for run in runs)
    any_estimated = any(run.get("estimated") for run in runs)
    exact = sum(1 for run in runs if not run.get("estimated"))
    est = sum(1 for run in runs if run.get("estimated"))

    model_counts: dict[str, int] = {}
    for run in runs:
        model = str(run.get("model", "")) or "unknown"
        model_counts[model] = model_counts.get(model, 0) + 1
    models = ", ".join(
        f"`{sanitize_comment_text(model, limit=60)}` ×{count}"
        for model, count in sorted(model_counts.items())
    )

    breakdown = f"{len(runs)} runs ({exact} exact, {est} est.)"
    return (
        "**Cumulative (this PR):** "
        f"{_fmt_tokens(total_tokens, estimated=any_estimated)} · "
        f"{_fmt_cost(total_cost, estimated=any_estimated)} · "
        f"{breakdown} · models: {models}"
    )


def _format_previous_runs(*, runs: list[dict[str, Any]]) -> str:
    """Render prior runs in a collapsible with each run's mechanics."""
    lines = [f"<details><summary>🕔 Previous runs ({len(runs)})</summary>", ""]
    for index, run in enumerate(runs, start=1):
        estimated = bool(run.get("estimated"))
        tokens = _fmt_tokens(int(run.get("total", 0)), estimated=estimated)
        cost = _fmt_cost(float(run.get("cost", 0.0)), estimated=estimated)
        timestamp = sanitize_comment_text(str(run.get("timestamp", "")), limit=40)
        model = sanitize_comment_text(str(run.get("model", "")), limit=60)
        findings = (
            f"🔴 {run.get('p1', 0)} · 🟠 {run.get('p2', 0)} · 🟡 {run.get('p3', 0)}"
        )
        partial = " · ⚠️ partial" if run.get("partial") else ""
        lines.append(
            f"{index}. `{model}` · depth {run.get('depth', '?')} · {tokens} · "
            f"{cost} · {findings}{partial} — {timestamp}",
        )
    lines.extend(["", "</details>"])
    return "\n".join(lines)


def _elide_low_value_sections(*, body: str) -> str:
    """Drop collapsible boilerplate before touching the Findings section.

    Args:
        body: Sticky comment body without the state block.

    Returns:
        Body with lower-priority sections removed when over the cap.
    """
    trimmed = body
    for pattern in (_PREVIOUS_RUNS_RE, _RUN_MECHANICS_RE, _CHECKLIST_APPENDIX_RE):
        if len(trimmed) <= MAX_COMMENT_CHARS:
            break
        trimmed = pattern.sub("", trimmed, count=1)
    footer = f"\n\n{_FOOTER}"
    if len(trimmed) > MAX_COMMENT_CHARS and trimmed.endswith(footer):
        trimmed = trimmed[: -len(footer)]
    return trimmed


def _findings_omission_marker(*, dropped: int) -> str:
    """Render the explicit marker when findings are dropped by ``_cap_body``."""
    return (
        f"\n\n> ✂️ **{dropped} finding(s) omitted** to fit "
        "GitHub's size limit — see the workflow run log for the full list."
    )


def _cap_findings_section(*, body: str) -> str:
    """Preserve the Findings header and trim finding blocks from the tail.

    Args:
        body: Sticky comment body that is still over ``MAX_COMMENT_CHARS``.

    Returns:
        Body with as many finding blocks as fit and an explicit omission marker
        when any findings were dropped.
    """
    match = _FINDINGS_SECTION_RE.search(body)
    if not match:
        return body

    prefix = body[: match.start()]
    findings_header = match.group(1)
    findings_body = match.group(2)
    suffix = match.group(3)

    blocks = [
        block for block in _FINDING_BLOCK_START_RE.split(findings_body) if block.strip()
    ]
    if not blocks:
        return body

    assembled_header = prefix + findings_header
    kept: list[str] = []
    for _index, block in enumerate(blocks):
        trial_kept = kept + [block]
        dropped = len(blocks) - len(trial_kept)
        omission = _findings_omission_marker(dropped=dropped) if dropped else ""
        trial = assembled_header + "".join(trial_kept) + omission + suffix
        if len(trial) <= MAX_COMMENT_CHARS:
            kept = trial_kept
        elif not kept:
            # Always retain at least one finding block even when oversized.
            kept = [block]
            break
        else:
            break

    dropped = len(blocks) - len(kept)
    omission = _findings_omission_marker(dropped=dropped) if dropped else ""
    return assembled_header + "".join(kept) + omission + suffix


def _cap_body(*, body: str) -> str:
    """Truncate an over-long comment body, preserving Findings preferentially.

    When the assembled sticky comment still exceeds ``MAX_COMMENT_CHARS`` after
    upstream budgeting, lower-value sections (previous runs, run mechanics,
    checklist appendix, footer) are elided first. If the body is still over the
    cap, finding blocks are trimmed from the tail with an explicit omission
    marker rather than blunt tail truncation that can silently drop Findings.

    Args:
        body: Sticky comment body without the state block.

    Returns:
        Body trimmed to ``MAX_COMMENT_CHARS`` with explicit markers when content
        was dropped.
    """
    if len(body) <= MAX_COMMENT_CHARS:
        return body

    trimmed = _elide_low_value_sections(body=body)
    if len(trimmed) <= MAX_COMMENT_CHARS:
        return trimmed

    capped = _cap_findings_section(body=trimmed)
    if len(capped) <= MAX_COMMENT_CHARS:
        return capped

    notice = "\n\n> ✂️ Comment truncated to fit GitHub's size limit."
    keep = MAX_COMMENT_CHARS - len(notice)
    return capped[:keep].rstrip() + notice


def parse_review_state(*, body: str) -> list[dict[str, Any]]:
    """Extract prior run records from a sticky comment's state block.

    Args:
        body: Existing sticky comment body.

    Returns:
        List of run records, or an empty list when no valid state is present.
    """
    start = body.find(STATE_MARKER_PREFIX)
    if start < 0:
        return []
    after = body[start + len(STATE_MARKER_PREFIX) :]
    end = after.rfind(STATE_MARKER_SUFFIX)
    raw = after[:end] if end >= 0 else after
    try:
        state = json.loads(raw.strip())
    except (json.JSONDecodeError, ValueError):
        return []
    runs = state.get("runs") if isinstance(state, dict) else None
    if not isinstance(runs, list):
        return []
    return [run for run in runs if isinstance(run, dict)]

"""Sticky-comment assembly, state, and size capping for GitHub reviews."""

from __future__ import annotations

from typing import Any

from lintro.ai.review.enums.checklist_display import ChecklistDisplay
from lintro.ai.review.enums.review_verdict import ReviewVerdict
from lintro.ai.review.finding_matcher import derive_verdict, match_findings
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
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.run_record import RunRecord
from lintro.ai.review.review_state_codec import (
    decode_state,
    prune_state_to_fit,
    render_state_block,
    renumber_if_legacy_v1,
)


def build_sticky_comment(
    *,
    result: ReviewResult,
    prior_runs: list[dict[str, Any]] | None = None,
    prior_state: ReviewState | None = None,
    checklist_display: ChecklistDisplay = ChecklistDisplay.OFF,
    question_map: dict[int, str] | None = None,
    diff_lines: dict[str, set[int]] | None = None,
    head_sha: str = "",
    transport: str = "",
    auth_mode: str = "",
) -> str:
    """Compose the full sticky PR comment body, including cumulative telemetry.

    Non-diff-mappable ("fallback") findings — whose only surface is this sticky
    comment — render first in the findings section. When the assembled body
    exceeds ``MAX_COMMENT_CHARS`` the findings section is re-rendered against a
    character budget so overflow is dropped explicitly (a visible marker names
    the count) and only ever falls on findings that also post inline.

    This round's findings are matched against the prior state so the persisted
    v2 blob carries each finding's identity, first-seen round, and resolution
    provenance for downstream surfaces.

    Args:
        result: Current review result.
        prior_runs: Legacy run records recovered from the previous sticky
            comment's state block. Ignored when ``prior_state`` is given.
        prior_state: Full state decoded from the previous sticky comment.
            ``None`` for the first run on a PR.
        checklist_display: Structured checklist visibility mode.
        question_map: Prompt id to question text for the checklist appendix.
        diff_lines: Diff line map used to order fallback findings first and to
            decide which findings are safe to truncate. ``None`` treats all
            findings as fallback.
        head_sha: Head commit sha reviewed in this round; stamped onto findings
            resolved by this round.
        transport: Provider transport used for this round.
        auth_mode: Authentication mode used by the transport.

    Returns:
        Complete Markdown body carrying the hidden marker and state block.
    """
    state = prior_state if prior_state is not None else _state_from_runs(prior_runs)
    round_number = state.next_round
    match = match_findings(
        previous=state,
        findings=result.findings,
        round_number=round_number,
        head_sha=head_sha,
    )
    prior = list(state.runs)
    current = _run_record(
        result=result,
        round_number=round_number,
        head_sha=head_sha,
        transport=transport,
        auth_mode=auth_mode,
        verdict=derive_verdict(findings=match.records),
    )
    combined_runs = [*prior, current]
    all_runs = combined_runs[-MAX_STORED_RUNS:]
    runs_dropped = len(all_runs) < len(combined_runs)

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
    new_state = ReviewState(
        runs=tuple(all_runs),
        findings=match.records,
        truncated=state.truncated or runs_dropped,
    )
    return body + render_state_block(
        state=prune_state_to_fit(state=new_state, body=body),
    )


def _state_from_runs(prior_runs: list[dict[str, Any]] | None) -> ReviewState:
    """Build a state object from legacy ``prior_runs`` mappings.

    Args:
        prior_runs: Run mappings recovered from a previous sticky comment, or
            ``None``.

    Returns:
        A state carrying those runs and no finding history.
    """
    runs = tuple(RunRecord.from_dict(run) for run in prior_runs or [])
    return ReviewState(runs=renumber_if_legacy_v1(runs=runs))


def _run_record(
    *,
    result: ReviewResult,
    round_number: int,
    head_sha: str,
    transport: str,
    auth_mode: str,
    verdict: ReviewVerdict,
) -> RunRecord:
    """Build a machine-readable run record from a review result.

    Args:
        result: Current review result.
        round_number: 1-based round number for this run.
        head_sha: Head commit sha reviewed in this round.
        transport: Provider transport used for this round.
        auth_mode: Authentication mode used by the transport.
        verdict: Readiness verdict derived from the open findings.

    Returns:
        The run record persisted in the state blob.
    """
    metadata = result.metadata
    counts = _severity_counts(findings=result.findings)
    usage = metadata.token_usage
    return RunRecord(
        round=round_number,
        timestamp=metadata.timestamp,
        sha=head_sha,
        model=metadata.model,
        provider=metadata.provider,
        transport=transport,
        auth_mode=auth_mode,
        depth=metadata.depth,
        strictness=metadata.strictness,
        files_reviewed=metadata.files_reviewed,
        files_skipped=max(metadata.files_total - metadata.files_reviewed, 0),
        checks=metadata.checklist_items,
        duration=metadata.duration_seconds,
        prompt=int(usage.get("prompt", 0)),
        completion=int(usage.get("completion", 0)),
        total=int(usage.get("total", 0)),
        cost=metadata.cost_estimate_usd,
        estimated=bool(metadata.token_usage_estimated),
        verdict=verdict,
        p1=counts[Severity.P1],
        p2=counts[Severity.P2],
        p3=counts[Severity.P3],
        partial=bool(metadata.partial),
        chunks_reviewed=metadata.chunks_reviewed,
        chunks_total=metadata.chunks_total,
    )


def _format_cumulative_header(*, runs: list[RunRecord]) -> str:
    """Render the always-visible cumulative telemetry header for the PR."""
    total_tokens = sum(run.total for run in runs)
    total_cost = sum(run.cost for run in runs)
    any_estimated = any(run.estimated for run in runs)
    exact = sum(1 for run in runs if not run.estimated)
    est = sum(1 for run in runs if run.estimated)

    model_counts: dict[str, int] = {}
    for run in runs:
        model = run.model or "unknown"
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


def _format_previous_runs(*, runs: list[RunRecord]) -> str:
    """Render prior runs in a collapsible with each run's mechanics."""
    lines = [f"<details><summary>🕔 Previous runs ({len(runs)})</summary>", ""]
    for index, run in enumerate(runs, start=1):
        tokens = _fmt_tokens(run.total, estimated=run.estimated)
        cost = _fmt_cost(run.cost, estimated=run.estimated)
        timestamp = sanitize_comment_text(run.timestamp, limit=40)
        model = sanitize_comment_text(run.model, limit=60)
        findings = f"🔴 {run.p1} · 🟠 {run.p2} · 🟡 {run.p3}"
        partial = " · ⚠️ partial" if run.partial else ""
        lines.append(
            f"{index}. `{model}` · depth {run.depth} · {tokens} · "
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

    Compatibility wrapper over :func:`parse_review_state_v2` for callers that
    only need the run history as plain mappings.

    Args:
        body: Existing sticky comment body.

    Returns:
        List of run records, or an empty list when no valid state is present.
    """
    return [run.to_dict() for run in parse_review_state_v2(body=body).runs]


def parse_review_state_v2(*, body: str) -> ReviewState:
    """Decode the full v2 review state from a sticky comment's state block.

    v1 blobs are migrated in place; a missing, malformed, or unknown-version
    blob yields an empty state rather than raising.

    Args:
        body: Existing sticky comment body.

    Returns:
        The decoded review state.
    """
    return decode_state(body=body)

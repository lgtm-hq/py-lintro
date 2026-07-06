"""GitHub PR posting adapter for AI review results.

Renders a rich, telemetry-informative sticky comment (one per PR, updated in
place) with a severity-count header, TL;DR, per-finding blocks (severity color
emoji, category/confidence chips, collapsible cause/fix), an always-visible
cumulative telemetry header, per-run mechanics with exact vs approximate (``~``)
labeling, and a machine-readable state block. All model-derived text is
sanitized (``@mentions`` neutralized, size capped) since it comes from an
untrusted PR diff.
"""

from __future__ import annotations

import json
import re
from typing import Any

from loguru import logger

from lintro.ai.integrations.github_pr import GitHubPRReporter
from lintro.ai.review.checklist_display import (
    cleared_answers,
    format_review_questions_markdown,
    orphan_concerns,
    questions_for_finding,
)
from lintro.ai.review.enums.checklist_display import ChecklistDisplay
from lintro.ai.review.errors_taxonomy import (
    KIND_COPY,
    ReviewErrorKind,
    classify_provider_error,
    resolve_cause_text,
)
from lintro.ai.review.models.review_finding import ReviewFinding, Severity
from lintro.ai.review.models.review_metadata import ReviewMetadata
from lintro.ai.review.models.review_result import ReviewResult

__all__ = [
    "STATE_MARKER_PREFIX",
    "STICKY_MARKER",
    "build_sticky_comment",
    "format_error_comment",
    "format_finding_comment",
    "format_review_summary",
    "format_run_mechanics",
    "parse_review_state",
    "post_review_error_to_github",
    "post_review_to_github",
    "sanitize_comment_text",
]

STICKY_MARKER = "<!-- lintro-ai-review -->"
STATE_MARKER_PREFIX = "<!-- lintro-ai-review-state:"
STATE_MARKER_SUFFIX = "-->"
STATE_VERSION = 1

# GitHub rejects comment bodies over 65,536 characters; stay well under.
MAX_COMMENT_CHARS = 60_000
# Cap how many run records are retained in the sticky state block.
MAX_STORED_RUNS = 30
# Slack reserved when budgeting the findings section against the comment cap,
# covering the truncation marker, section joins, and the final _cap_body notice.
_TRUNCATION_MARGIN = 400

_SEVERITY_EMOJI: dict[Severity, str] = {
    Severity.P1: "🔴",
    Severity.P2: "🟠",
    Severity.P3: "🟡",
}

_FOOTER = (
    "<sub>🤖 Automated review by lintro · not a substitute for human review · "
    "`~` = approximate (estimated locally; provider did not report token "
    "usage)</sub>"
)

_MENTION_RE = re.compile(r"(?<![\w/@.-])@(?=[A-Za-z0-9])")


def sanitize_comment_text(text: str, *, limit: int | None = None) -> str:
    """Neutralize untrusted model output for safe rendering in a PR comment.

    Breaks GitHub ``@mentions`` (so injected text cannot ping or notify users)
    by inserting a zero-width space after a leading ``@``, and optionally caps
    the length. The input originates from an untrusted PR diff, so this is a
    security boundary, not cosmetic.

    Args:
        text: Raw model-derived text.
        limit: Optional maximum character length before truncation.

    Returns:
        Sanitized text safe to embed in Markdown.
    """
    cleaned = _MENTION_RE.sub("@​", text or "")
    if limit is not None and len(cleaned) > limit:
        cleaned = cleaned[: max(limit - 1, 0)].rstrip() + "…"
    return cleaned


def _chip(text: str) -> str:
    """Render a value as an inline code chip, escaping backticks."""
    safe = sanitize_comment_text(text, limit=60).replace("`", "'")
    return f"`{safe}`"


def _severity_badge(*, severity: Severity) -> str:
    """Render a severity as a color emoji plus bold label."""
    emoji = _SEVERITY_EMOJI.get(severity, "⚪")
    return f"{emoji} **{severity.value}**"


def _fmt_int(value: int) -> str:
    """Format an integer with thousands separators."""
    return f"{value:,}"


def _fmt_cost(value: float, *, estimated: bool) -> str:
    """Format a USD cost, prefixing ``~`` when the value is estimated."""
    prefix = "~" if estimated else ""
    return f"{prefix}${value:.4f}"


def _fmt_tokens(total: int, *, estimated: bool) -> str:
    """Format a token count, prefixing ``~`` when estimated."""
    prefix = "~" if estimated else ""
    return f"{prefix}{_fmt_int(total)} tok"


def format_run_mechanics(*, metadata: ReviewMetadata) -> str:
    """Format the per-run mechanics footer for a single review run.

    Args:
        metadata: Review run metadata.

    Returns:
        Markdown describing model, provider, tokens, cost, depth, and duration.
        Estimated token/cost figures are prefixed with ``~``.
    """
    estimated = metadata.token_usage_estimated
    total_tokens = int(metadata.token_usage.get("total", 0))
    prompt_tokens = int(metadata.token_usage.get("prompt", 0))
    completion_tokens = int(metadata.token_usage.get("completion", 0))
    source = "estimated" if estimated else "provider-reported"
    parts = [
        f"**Model:** `{sanitize_comment_text(metadata.model, limit=60)}`",
        f"**Provider:** `{sanitize_comment_text(metadata.provider, limit=40)}`",
        f"**Depth:** {metadata.depth}",
        (
            f"**Tokens:** {_fmt_tokens(total_tokens, estimated=estimated)} "
            f"(in {_fmt_int(prompt_tokens)} / out {_fmt_int(completion_tokens)}, "
            f"{source})"
        ),
        f"**Est. cost:** {_fmt_cost(metadata.cost_estimate_usd, estimated=estimated)}",
        f"**Duration:** {metadata.duration_seconds:.1f}s",
    ]
    return " · ".join(parts)


def _severity_counts(*, findings: tuple[ReviewFinding, ...]) -> dict[Severity, int]:
    """Count findings by severity."""
    counts: dict[Severity, int] = {Severity.P1: 0, Severity.P2: 0, Severity.P3: 0}
    for finding in findings:
        counts[finding.severity] = counts.get(finding.severity, 0) + 1
    return counts


def _count_row(*, counts: dict[Severity, int]) -> list[str]:
    """Render the severity count table."""
    return [
        "| 🔴 P1 | 🟠 P2 | 🟡 P3 |",
        "|:-:|:-:|:-:|",
        (
            f"| **{counts[Severity.P1]}** | **{counts[Severity.P2]}** | "
            f"**{counts[Severity.P3]}** |"
        ),
    ]


def format_finding_comment(
    *,
    finding: ReviewFinding,
    checklist_display: ChecklistDisplay = ChecklistDisplay.OFF,
    question_map: dict[int, str] | None = None,
) -> str:
    """Format a review finding as a rich GitHub markdown comment.

    Used both for inline review comments and for the summary's findings list.
    Renders the severity as a color emoji, category and confidence as ``code``
    chips, the cause/fix in a collapsible ``<details>``, and a GitHub
    ``suggestion`` block when the finding carries concrete replacement code.

    Args:
        finding: Review finding to format.
        checklist_display: Structured checklist visibility mode.
        question_map: Prompt id to question text for linked display.

    Returns:
        Markdown comment body.
    """
    prompt_questions = question_map or {}
    title = sanitize_comment_text(finding.title, limit=200)
    description = sanitize_comment_text(finding.description, limit=2000)
    cause = sanitize_comment_text(finding.cause, limit=2000)
    fix = sanitize_comment_text(finding.fix, limit=2000)

    header = (
        f"{_severity_badge(severity=finding.severity)} · "
        f"{_chip(finding.category)} · {_chip(f'{finding.confidence} confidence')}"
    )
    lines = [header, "", f"### {title}", "", description]

    detail: list[str] = []
    if cause.strip():
        detail.append(f"**Cause:** {cause}")
    if fix.strip():
        detail.append(f"**Fix:** {fix}")
    if detail:
        lines.extend(
            [
                "",
                "<details><summary>💡 Why this matters &amp; how to fix</summary>",
                "",
                "\n\n".join(detail),
                "",
                "</details>",
            ],
        )

    suggestion = _suggestion_block(finding=finding)
    if suggestion:
        lines.extend(["", suggestion])

    body = "\n".join(lines)
    if checklist_display in {ChecklistDisplay.LINKED, ChecklistDisplay.ALL}:
        linked = questions_for_finding(
            finding=finding,
            question_map=prompt_questions,
        )
        body += format_review_questions_markdown(questions=linked)
    body += "\n\n<sub>lintro · " + _chip(finding.category) + "</sub>"
    return body


def _suggestion_block(*, finding: ReviewFinding) -> str:
    """Render a GitHub ``suggestion`` block when concrete code is available."""
    code = finding.suggested_code
    if not code or not code.strip():
        return ""
    # Neutralize fence break-out and @mentions in untrusted model code. The
    # suggestion body renders as Markdown, so an unescaped `@user` still pings.
    safe = code.replace("```", "``​`")
    safe = _MENTION_RE.sub("@​", safe)
    return "```suggestion\n" + safe + "\n```"


def format_review_summary(
    *,
    result: ReviewResult,
    checklist_display: ChecklistDisplay = ChecklistDisplay.OFF,
    question_map: dict[int, str] | None = None,
    diff_lines: dict[str, set[int]] | None = None,
    findings_char_budget: int | None = None,
) -> str:
    """Format the per-run review summary section.

    Produces the scannable body for a single run: header line, partial-state
    note, severity count table, TL;DR, a compact findings list with collapsible
    detail, and (optionally) the checklist appendix.

    Args:
        result: Review result to summarize.
        checklist_display: Structured checklist visibility mode.
        question_map: Prompt id to question text for the checklist appendix.
        diff_lines: Diff line map used to order fallback findings first in the
            findings section. ``None`` treats all findings as fallback.
        findings_char_budget: Optional soft character budget for the findings
            section; overflow is replaced by an explicit truncation marker.

    Returns:
        Markdown summary section body.
    """
    metadata = result.metadata
    counts = _severity_counts(findings=result.findings)
    est = metadata.token_usage_estimated
    head_bits = [
        f"**{metadata.files_reviewed} files** reviewed",
        f"depth {metadata.depth}",
        f"`{sanitize_comment_text(metadata.model, limit=60)}`",
        _fmt_cost(metadata.cost_estimate_usd, estimated=est),
    ]
    lines = [
        "## 🔎 Lintro Review",
        "",
        "> " + " · ".join(head_bits),
    ]
    if metadata.partial:
        reason = sanitize_comment_text(
            metadata.stopped_reason or "incomplete",
            limit=60,
        )
        if metadata.chunks_reviewed <= 0:
            note = (
                f"> ⚠️ **Partial review** — stopped at {reason} before "
                f"reviewing any of {metadata.chunks_total} chunks. No findings "
                "were produced. Raise `ai.max_cost_usd` or narrow `--path`, then "
                "re-run."
            )
        else:
            note = (
                f"> ⚠️ **Partial review** — stopped at {reason} after "
                f"{metadata.chunks_reviewed} of {metadata.chunks_total} "
                "chunks. Findings below cover only the reviewed portion. Raise "
                "`ai.max_cost_usd` or narrow `--path` to review the rest."
            )
        lines.extend(["", note])

    lines.extend(["", *_count_row(counts=counts)])

    summary_text = sanitize_comment_text(result.summary or "(no summary)", limit=4000)
    lines.extend(["", f"**TL;DR** — {summary_text}"])

    lines.extend(
        _format_findings_section(
            findings=result.findings,
            checklist_display=checklist_display,
            question_map=question_map or {},
            diff_lines=diff_lines,
            char_budget=findings_char_budget,
        ),
    )

    lines.append(f"\n**Structured checks:** {metadata.checklist_items}")

    if checklist_display == ChecklistDisplay.ALL:
        lines.extend(_format_checklist_appendix_markdown(result=result))

    return "\n".join(lines)


def _is_diff_mappable(
    *,
    finding: ReviewFinding,
    diff_lines: dict[str, set[int]] | None,
) -> bool:
    """Report whether a finding maps onto a line inside the PR diff.

    A diff-mappable finding also posts as an inline review comment, so the
    sticky comment is not its only surface. A non-diff-mappable ("fallback")
    finding has no inline path and must survive sticky-comment truncation.

    Args:
        finding: Review finding to classify.
        diff_lines: Map of repo-relative path to the set of diff-covered line
            numbers, or ``None`` when the diff is unavailable (all findings are
            then treated as fallback).

    Returns:
        True when the finding lands on a diff-covered line, else False.
    """
    rel = finding.file.removeprefix("./").replace("\\", "/")
    if not rel or finding.line <= 0 or diff_lines is None:
        return False
    return finding.line in diff_lines.get(rel, set())


def _finding_block(
    *,
    finding: ReviewFinding,
    checklist_display: ChecklistDisplay,
    question_map: dict[int, str],
) -> list[str]:
    """Render the markdown lines for a single finding in the findings list."""
    location = _location_label(finding=finding)
    title = sanitize_comment_text(finding.title, limit=200)
    headline = (
        f"{_severity_badge(severity=finding.severity)} · "
        f"{_chip(finding.category)} — **{title}**"
    )
    if location:
        headline += f" · {location}"
    body = format_finding_comment(
        finding=finding,
        checklist_display=checklist_display,
        question_map=question_map,
    )
    return [
        "",
        headline,
        "",
        "<details><summary>Details</summary>",
        "",
        body,
        "",
        "</details>",
    ]


def _format_findings_section(
    *,
    findings: tuple[ReviewFinding, ...],
    checklist_display: ChecklistDisplay,
    question_map: dict[int, str],
    diff_lines: dict[str, set[int]] | None = None,
    char_budget: int | None = None,
) -> list[str]:
    """Render a compact, collapsible list of all findings.

    Non-diff-mappable ("fallback") findings render first: they have no inline
    surface, so if truncation must drop anything it only drops findings that
    also exist as inline comments. When ``char_budget`` is set and the rendered
    blocks would overflow it, rendering stops early and an explicit marker names
    how many findings were dropped so nothing silently vanishes.

    Args:
        findings: Findings to render.
        checklist_display: Structured checklist visibility mode.
        question_map: Prompt id to question text for linked display.
        diff_lines: Diff line map used to order fallback findings first. ``None``
            treats every finding as fallback (preserving severity ordering).
        char_budget: Optional soft character budget for the finding blocks. When
            exceeded, remaining *diff-mappable* findings are replaced by a
            truncation marker (they still exist as inline comments). Fallback
            findings are never budget-truncated — they have no other surface.

    Returns:
        Markdown lines for the findings section.
    """
    if not findings:
        return ["", "### Findings", "", "✅ No actionable findings."]

    ordered = sorted(
        findings,
        key=lambda f: (
            _is_diff_mappable(finding=f, diff_lines=diff_lines),
            f.severity.value,
            f.file,
            f.line,
        ),
    )
    lines = ["", f"### Findings ({len(findings)})"]
    used = 0
    rendered = 0
    for index, finding in enumerate(ordered):
        mappable = _is_diff_mappable(finding=finding, diff_lines=diff_lines)
        block = _finding_block(
            finding=finding,
            checklist_display=checklist_display,
            question_map=question_map,
        )
        block_len = len("\n".join(block))
        # Only diff-mappable findings may be budget-truncated: they also post as
        # inline comments, so the "see inline comments" marker is accurate for
        # them. Fallback (non-diff-mappable) findings have no other surface and
        # are always rendered. Since fallback findings sort first, anything
        # dropped here is guaranteed diff-mappable. Always render at least one
        # finding so a single oversized first block is not itself dropped.
        if (
            char_budget is not None
            and mappable
            and rendered > 0
            and used + block_len > char_budget
        ):
            dropped = len(ordered) - index
            lines.extend(
                [
                    "",
                    f"> ✂️ **{dropped} more finding(s) truncated** to fit "
                    "GitHub's size limit — see the inline comments and workflow "
                    "logs.",
                ],
            )
            break
        lines.extend(block)
        used += block_len
        rendered += 1
    return lines


def _location_label(*, finding: ReviewFinding) -> str:
    """Format a ``file:line`` code label for a finding, or empty when unknown."""
    if not finding.file:
        return ""
    safe = sanitize_comment_text(finding.file, limit=200)
    if finding.line > 0:
        return f"`{safe}:{finding.line}`"
    return f"`{safe}`"


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


def _cap_body(*, body: str) -> str:
    """Truncate an over-long comment body, appending a notice."""
    if len(body) <= MAX_COMMENT_CHARS:
        return body
    notice = "\n\n> ✂️ Comment truncated to fit GitHub's size limit."
    keep = MAX_COMMENT_CHARS - len(notice)
    return body[:keep].rstrip() + notice


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


def format_error_comment(
    *,
    error: Exception,
    provider: str | None = None,
    metadata: ReviewMetadata | None = None,
    prior_runs: list[dict[str, Any]] | None = None,
) -> str:
    """Format a provider/API error as a clear PR comment.

    Classifies the error provider-aware, against the underlying provider cause
    (unwrapping any orchestrator wrapper), into a canonical
    :class:`~lintro.ai.review.errors_taxonomy.ReviewErrorKind` and renders the
    specific sticky for that kind — for example, depleted credits render as a
    clear "top up or lower ai.max_cost_usd" message rather than a generic
    "review aborted". The real underlying cause text is always surfaced.

    Args:
        error: The exception raised during review.
        provider: Provider identifier used for provider-aware classification.
            Falls back to ``metadata.provider`` when omitted.
        metadata: Optional review metadata for a mechanics footer.
        prior_runs: Run records recovered from the previous sticky comment.
            Re-emitted so a transient error does not reset cumulative telemetry.

    Returns:
        Markdown comment body describing the failure and next steps.
    """
    resolved_provider = provider or (metadata.provider if metadata else "") or ""
    kind = classify_provider_error(provider=resolved_provider, error=error)
    detail, guidance = _render_error_copy(
        kind=kind,
        error=error,
        provider=resolved_provider,
    )
    lines = [
        STICKY_MARKER,
        "## 🔎 Lintro Review",
        "",
        f"> ❌ **Review could not complete** — {detail}",
        "",
        guidance,
    ]
    if metadata is not None and metadata.model:
        lines.extend(["", "<sub>" + format_run_mechanics(metadata=metadata) + "</sub>"])
    lines.extend(["", _FOOTER])
    body = "\n".join(lines)
    if prior_runs:
        body += _render_state_block(runs=list(prior_runs))
    return body


def _render_error_copy(
    *,
    kind: ReviewErrorKind,
    error: Exception,
    provider: str,
) -> tuple[str, str]:
    """Build the (detail, guidance) pair for a classified review error.

    The detail is prefixed with the provider label when known and always
    carries the surfaced underlying cause text. For an unknown kind the cause
    text is the primary signal; for known kinds it is appended so the real
    provider message is never lost.

    Args:
        kind: Resolved canonical error kind.
        error: The original exception (possibly a wrapper).
        provider: Provider identifier, used only as a display label.

    Returns:
        Tuple of ``(detail, guidance)`` markdown strings.
    """
    message, guidance = KIND_COPY[kind]
    cause = sanitize_comment_text(resolve_cause_text(error=error), limit=500)
    label = f"`{sanitize_comment_text(provider, limit=40)}` " if provider else ""

    if kind is ReviewErrorKind.UNKNOWN:
        detail = f"{label}{message}: {cause}" if cause else f"{label}{message}"
        return detail, guidance

    detail = f"{label}{message}"
    if cause:
        detail += f" (provider reported: {cause})"
    return detail, guidance


def post_review_to_github(
    *,
    result: ReviewResult,
    pr_number: int | None = None,
    repo: str | None = None,
    reporter: GitHubPRReporter | None = None,
    checklist_display: ChecklistDisplay = ChecklistDisplay.OFF,
    question_map: dict[int, str] | None = None,
) -> bool:
    """Post (or update) the sticky review comment and inline findings.

    Maintains a single sticky comment per PR (identified by ``STICKY_MARKER``),
    updated in place with cumulative telemetry. Diff-mappable findings are also
    posted as inline review comments carrying suggestion blocks.

    Args:
        result: Review result to post.
        pr_number: Optional PR number override.
        repo: Optional repository override (owner/name).
        reporter: Optional preconfigured GitHub reporter.
        checklist_display: Structured checklist visibility mode.
        question_map: Prompt id to question text for linked display.

    Returns:
        True when posting succeeded; False on failure or when GitHub context is
        unavailable.
    """
    gh_reporter = reporter or GitHubPRReporter(pr_number=pr_number, repo=repo)
    if not gh_reporter.is_available():
        logger.warning("GitHub PR context not available — skipping review posting")
        return False

    prompt_questions = question_map or {}
    comment_id, prior_runs = _load_prior_runs(reporter=gh_reporter)
    diff_lines = gh_reporter.fetch_pr_diff_lines()
    body = build_sticky_comment(
        result=result,
        prior_runs=prior_runs,
        checklist_display=checklist_display,
        question_map=prompt_questions,
        diff_lines=diff_lines,
    )

    success = _upsert_sticky(reporter=gh_reporter, body=body, comment_id=comment_id)

    inline_findings, _fallback = _partition_findings(
        findings=result.findings,
        diff_lines=diff_lines,
    )
    if inline_findings and not _post_inline_findings(
        reporter=gh_reporter,
        findings=inline_findings,
        checklist_display=checklist_display,
        question_map=prompt_questions,
    ):
        success = False

    return success


def post_review_error_to_github(
    *,
    error: Exception,
    provider: str | None = None,
    metadata: ReviewMetadata | None = None,
    pr_number: int | None = None,
    repo: str | None = None,
    reporter: GitHubPRReporter | None = None,
) -> bool:
    """Post (or update) the sticky comment with a formatted API-error message.

    Args:
        error: The exception raised during review.
        provider: Provider identifier used for provider-aware classification.
        metadata: Optional metadata for a mechanics footer.
        pr_number: Optional PR number override.
        repo: Optional repository override (owner/name).
        reporter: Optional preconfigured GitHub reporter.

    Returns:
        True when posting succeeded; False otherwise.
    """
    gh_reporter = reporter or GitHubPRReporter(pr_number=pr_number, repo=repo)
    if not gh_reporter.is_available():
        logger.warning("GitHub PR context not available — skipping error posting")
        return False
    comment_id, prior_runs = _load_prior_runs(reporter=gh_reporter)
    body = format_error_comment(
        error=error,
        provider=provider,
        metadata=metadata,
        prior_runs=prior_runs,
    )
    return _upsert_sticky(reporter=gh_reporter, body=body, comment_id=comment_id)


def _load_prior_runs(
    *,
    reporter: GitHubPRReporter,
) -> tuple[int | None, list[dict[str, Any]]]:
    """Locate the sticky comment and parse its prior run records.

    Args:
        reporter: GitHub reporter used to list PR comments.

    Returns:
        Tuple of ``(comment_id, run_records)``; the id is ``None`` when no
        sticky comment exists yet.
    """
    found = reporter.find_issue_comment(marker=STICKY_MARKER)
    if found is None:
        return None, []
    comment_id, prior_body = found
    return comment_id, parse_review_state(body=prior_body)


def _upsert_sticky(
    *,
    reporter: GitHubPRReporter,
    body: str,
    comment_id: int | None,
) -> bool:
    """Update the sticky comment in place, or create it when absent."""
    if comment_id is not None:
        return reporter.update_issue_comment(comment_id=comment_id, body=body)
    return reporter.post_issue_comment(body)


def _format_checklist_appendix_markdown(*, result: ReviewResult) -> list[str]:
    """Build cleared/orphan checklist appendix lines for markdown."""
    cleared = cleared_answers(answers=result.checklist)
    orphans = orphan_concerns(
        answers=result.checklist,
        findings=result.findings,
    )
    lines = ["", f"### Cleared checks ({len(cleared)})"]
    if cleared:
        for answer in cleared:
            question = sanitize_comment_text(
                answer.question or f"(checklist item {answer.id})",
                limit=300,
            )
            lines.append(f"- ✓ {question}")
    else:
        lines.append("- (none)")

    lines.extend(["", f"### Checklist concerns without findings ({len(orphans)})"])
    if orphans:
        for answer in orphans:
            question = sanitize_comment_text(
                answer.question or f"(checklist item {answer.id})",
                limit=300,
            )
            evidence = sanitize_comment_text(answer.evidence, limit=200).replace(
                "|",
                "\\|",
            )
            lines.append(f"- {question}")
            if evidence.strip():
                lines.append(f"  - {evidence}")
    else:
        lines.append("- (none — good)")
    return lines


def _partition_findings(
    *,
    findings: tuple[ReviewFinding, ...],
    diff_lines: dict[str, set[int]] | None,
) -> tuple[list[ReviewFinding], list[ReviewFinding]]:
    """Split findings into inline-capable and fallback groups.

    Args:
        findings: Findings to partition.
        diff_lines: Diff line map; ``None`` classifies every finding as fallback.

    Returns:
        Tuple of ``(inline, fallback)`` finding lists.
    """
    inline: list[ReviewFinding] = []
    fallback: list[ReviewFinding] = []

    for finding in findings:
        if _is_diff_mappable(finding=finding, diff_lines=diff_lines):
            inline.append(finding)
        else:
            fallback.append(finding)

    return inline, fallback


def _post_inline_findings(
    *,
    reporter: GitHubPRReporter,
    findings: list[ReviewFinding],
    checklist_display: ChecklistDisplay,
    question_map: dict[int, str],
) -> bool:
    """Post inline PR review comments for mappable findings."""
    comments: list[dict[str, Any]] = []
    for finding in findings:
        rel = finding.file.removeprefix("./").replace("\\", "/")
        comments.append(
            {
                "path": rel,
                "body": format_finding_comment(
                    finding=finding,
                    checklist_display=checklist_display,
                    question_map=question_map,
                ),
                "line": finding.line,
                "side": "RIGHT",
            },
        )

    if not comments:
        return True

    payload = {
        "event": "COMMENT",
        "body": "Lintro review findings",
        "comments": comments,
    }
    url = (
        f"{reporter.api_base}/repos/{reporter.repo}/pulls/"
        f"{reporter.pr_number}/reviews"
    )
    return reporter.api_request("POST", url, payload)

"""Finding-table and finding-fold renderers for the sticky board.

The sections a reviewer is actually here for: the round's delta table, the
open-findings index, the folded detail shown when an inline comment could not
be posted, and the resolved list.
"""

from __future__ import annotations

from lintro.ai.review.checklist_display import (
    format_review_questions_markdown,
    questions_for_finding,
)
from lintro.ai.review.convergence import score_trajectory
from lintro.ai.review.enums.checklist_display import ChecklistDisplay
from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.enums.review_verdict import ReviewVerdict
from lintro.ai.review.github_constants import _SEVERITY_EMOJI
from lintro.ai.review.github_contract import RenderLimits
from lintro.ai.review.github_notes import format_convergence_note
from lintro.ai.review.models.finding_match_result import FindingMatchResult
from lintro.ai.review.models.finding_record import FindingRecord
from lintro.ai.review.models.inline_post_failure import InlinePostFailure
from lintro.ai.review.models.review_finding import ReviewFinding
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.sticky_plan import StickyPlan
from lintro.ai.review.sticky.cells import (
    _cell,
    _delta_cell,
    _finding_cell,
    _inline_safe,
    _location,
    _plural,
    _severity_cell,
    _short_sha,
    _sorted_open_records,
)
from lintro.ai.review.sticky.constants import _QUESTION_EMOJI, _TITLE_LIMIT


def _findings_round_section(*, plan: StickyPlan, limits: RenderLimits) -> str:
    """Render the Findings heading, the convergence note, and the Δ table.

    Args:
        plan: Resolved inputs for the body being rendered. ``result`` is
            ``None`` on a state-only re-render; only the recorded convergence
            scores are read from ``runs``.
        limits: Per-section render limits.

    Returns:
        The Findings section.
    """
    match = plan.match
    result = plan.result
    round_number = plan.round_number
    head_sha = plan.head_sha
    verdict = plan.verdict
    repo = plan.repo
    pr_number = plan.pr_number
    runs = plan.runs
    open_records = _sorted_open_records(records=match.records, limit=limits.open)
    fixed_now = [
        record
        for record in match.records
        if record.status is FindingStatus.RESOLVED
        and record.resolved_round == round_number
    ]
    if limits.resolved is not None:
        fixed_now = fixed_now[: limits.resolved]
    coverage_label = _findings_coverage_label(result=result, verdict=verdict)
    short = _short_sha(sha=head_sha)
    sha_bit = f" · `{short}`" if short else ""
    open_count = sum(
        1 for record in match.records if record.status is FindingStatus.OPEN
    )
    heading = (
        f"### Findings · Round {round_number}{sha_bit} · {coverage_label} · "
        f"{open_count} open · {len(fixed_now)} fixed this round"
    )
    note = format_convergence_note(trajectory=score_trajectory(runs=tuple(runs)))
    if not open_records and not fixed_now:
        empty = [heading, "", "✅ Nothing open."]
        if note:
            empty.extend(["", note])
        return "\n".join(empty)
    lines = [heading]
    if note:
        lines.extend(["", note])
    lines.extend(
        [
            "",
            "| Δ | Sev | Finding | Where | Since |",
            "|:-:|:-:|---|---|---|",
        ],
    )
    for record in open_records:
        lines.append(
            f"| {_delta_cell(record=record, match=match)} "
            f"| {_severity_cell(record=record)} "
            f"| {_finding_cell(record=record, repo=repo, pr_number=pr_number)} "
            f"| `{_location(record=record)}` "
            f"| round {record.since_round} |",
        )
    for record in fixed_now:
        lines.append(
            f"| ✔ fixed "
            f"| {_severity_cell(record=record)} "
            f"| ~~{_cell(text=record.title, limit=_TITLE_LIMIT)}~~ "
            f"| `{_location(record=record)}` "
            f"| round {record.since_round} |",
        )
    dropped = open_count - len(open_records)
    if dropped > 0:
        lines.extend(
            [
                "",
                f"> ✂️ **{dropped} more open "
                f"{_plural(count=dropped, noun='finding')} not listed** to fit "
                "GitHub's size limit — see the inline comments and the workflow "
                "run log.",
            ],
        )
    return "\n".join(lines)


def _findings_coverage_label(
    *,
    result: ReviewResult | None,
    verdict: ReviewVerdict,
) -> str:
    """Return the coverage fragment for the Findings heading."""
    del verdict
    if result is None or result.coverage is None:
        return "coverage n/a"
    counts = result.coverage
    if counts.complete:
        return f"✅ {counts.covered_at_head}/{counts.eligible} at HEAD"
    return f"⚠️ {counts.covered_at_head}/{counts.eligible} files"


def _open_findings_section(
    *,
    records: list[FindingRecord],
    match: FindingMatchResult,
    total: int,
    repo: str = "",
    pr_number: int | None = None,
) -> str:
    """Render the open-findings index table.

    Titles only, one line each: the detail lives on the inline comments, and
    duplicating it here is what made the previous sticky unreadable. Each title
    links to that detail so the index is one click from the thread.

    Args:
        records: Open records to render, already ordered and limited.
        match: Cross-round matching outcome, for the ``Δ`` column.
        total: Total number of open findings before any limit was applied.
        repo: ``owner/name`` slug used to build the per-finding links.
        pr_number: Pull request number used for the same links.

    Returns:
        The ``Open findings`` section, always present so a reader never has to
        infer that nothing is open.
    """
    if total == 0:
        return "### Open findings (0)\n\n✅ Nothing open."

    lines = [
        f"### Open findings ({total})",
        "",
        "| Δ | Sev | Finding | Where | Since |",
        "|:-:|:-:|---|---|---|",
    ]
    for record in records:
        lines.append(
            f"| {_delta_cell(record=record, match=match)} "
            f"| {_severity_cell(record=record)} "
            f"| {_finding_cell(record=record, repo=repo, pr_number=pr_number)} "
            f"| `{_location(record=record)}` "
            f"| round {record.since_round} |",
        )
    dropped = total - len(records)
    if dropped > 0:
        lines.extend(
            [
                "",
                f"> ✂️ **{dropped} more open "
                f"{_plural(count=dropped, noun='finding')} not listed** to fit "
                "GitHub's size limit — see the inline comments and the workflow "
                "run log.",
            ],
        )
    return "\n".join(lines)


def _degraded_details(
    *,
    failure: InlinePostFailure | None,
    checklist_display: ChecklistDisplay,
    question_map: dict[int, str],
    limit: int | None = None,
) -> str:
    """Fold full finding detail into the sticky when inline posting failed.

    Rendered flat inside a single ``<details>``: the sticky's no-nesting rule
    means this cannot reuse the inline comment renderer, which carries its own
    collapsible.

    Args:
        failure: Findings whose inline comments could not be posted.
        checklist_display: Structured checklist visibility mode.
        question_map: Prompt id to question text for linked questions.
        limit: Maximum number of findings to fold in. Shares the open-finding
            limit so this section shrinks under the same size pressure instead
            of being left to blunt tail truncation.

    Returns:
        A single-level collapsible carrying each failed finding's detail, or an
        empty string when inline posting succeeded.
    """
    if failure is None or failure.is_empty:
        return ""

    shown = failure.findings if limit is None else failure.findings[:limit]
    lines = [
        f"<details><summary>📋 Details for {failure.count} "
        f"{_plural(count=failure.count, noun='finding')} not posted inline"
        "</summary>",
        "",
    ]
    for finding in shown:
        lines.extend(
            _folded_finding(
                finding=finding,
                checklist_display=checklist_display,
                question_map=question_map,
            ),
        )
    dropped = failure.count - len(shown)
    if dropped > 0:
        lines.append(
            f"> ✂️ **{dropped} more failed "
            f"{_plural(count=dropped, noun='finding')} not detailed** to fit "
            "GitHub's size limit — see the workflow run log.",
        )
    lines.extend(["", "</details>"])
    return "\n".join(lines)


def _folded_finding(
    *,
    finding: ReviewFinding,
    checklist_display: ChecklistDisplay,
    question_map: dict[int, str],
) -> list[str]:
    """Render one finding's detail flat, with no collapsible of its own.

    Args:
        finding: Finding to render.
        checklist_display: Structured checklist visibility mode.
        question_map: Prompt id to question text for linked questions.

    Returns:
        Markdown lines for the finding.
    """
    emoji = (
        _QUESTION_EMOJI if finding.is_question else _SEVERITY_EMOJI[finding.severity]
    )
    label = "question" if finding.is_question else finding.severity.value
    location = _inline_safe(text=finding.file, limit=200)
    where = f"`{location}:{finding.line}`" if finding.line > 0 else f"`{location}`"
    lines = [
        f"**{emoji} {label}** · `{_inline_safe(text=finding.category, limit=60)}`"
        f" — **{_inline_safe(text=finding.title, limit=_TITLE_LIMIT)}** · {where}",
        "",
        _inline_safe(text=finding.description, limit=2000),
    ]
    for heading, text in (("Cause", finding.cause), ("Fix", finding.fix)):
        body = _inline_safe(text=text, limit=2000).strip()
        if body:
            lines.extend(["", f"**{heading}:** {body}"])
    if checklist_display in {ChecklistDisplay.LINKED, ChecklistDisplay.ALL}:
        linked = format_review_questions_markdown(
            questions=questions_for_finding(
                finding=finding,
                question_map=question_map,
            ),
        )
        if linked.strip():
            lines.append(linked)
    lines.append("")
    return lines


def _resolved_section(*, records: list[FindingRecord], total: int) -> str:
    """Render the resolved-findings table with fixing-commit provenance.

    Args:
        records: Resolved records to render, already ordered and limited.
        total: Total number of resolved findings before any limit was applied.

    Returns:
        The ``Resolved`` section, or an empty string when nothing is resolved.
    """
    if total == 0:
        return ""

    lines = [
        f"### ✔ Resolved ({total})",
        "",
        "| Sev | Finding | Fixed in |",
        "|:-:|---|---|",
    ]
    for record in records:
        short = _short_sha(sha=record.resolved_sha)
        fixed_in = f"`{short}` · round {record.resolved_round}" if short else "—"
        lines.append(
            f"| {_severity_cell(record=record)} "
            f"| ~~{_cell(text=record.title, limit=_TITLE_LIMIT)}~~ "
            f"| {fixed_in} |",
        )
    dropped = total - len(records)
    if dropped > 0:
        lines.extend(
            [
                "",
                f"> ✂️ **{dropped} older resolved "
                f"{_plural(count=dropped, noun='finding')} not listed** "
                "(history truncated to fit GitHub's size limit).",
            ],
        )
    return "\n".join(lines)

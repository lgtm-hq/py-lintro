"""Mission-control section renderers for the sticky board.

One function per block of the board a reviewer scans: the header and its
readiness pill, the banners and one-line rows, the summary, and the model's
reasoning. Each returns Markdown or ``""`` to opt out, and the assembler
decides the order they appear in.
"""

from __future__ import annotations

from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.enums.review_verdict import ReviewVerdict
from lintro.ai.review.github_constants import _SEVERITY_EMOJI
from lintro.ai.review.github_notes import (
    format_coverage_limited_warning,
    format_cross_chunk_note,
    format_inline_post_note,
)
from lintro.ai.review.github_render import sanitize_comment_text
from lintro.ai.review.models.finding_match_result import FindingMatchResult
from lintro.ai.review.models.finding_record import FindingRecord
from lintro.ai.review.models.inline_post_failure import InlinePostFailure
from lintro.ai.review.models.review_finding import Severity
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.patch_validation import describe_suggestion_drops
from lintro.ai.review.sticky.cells import _plural
from lintro.ai.review.sticky.constants import (
    _REASONING_HEADINGS,
    _VERDICT_NOUNS,
    _VERDICT_SEVERITY,
    VERDICT_EMOJI,
)
from lintro.ai.review.verdict import (
    VERDICT_RUBRIC_FINE_PRINT,
    resolve_bullet_finding,
    verdict_label,
)


def _header(
    *,
    round_number: int,
    head_sha: str,
    verdict: ReviewVerdict = ReviewVerdict.READY,
) -> str:
    """Render the sticky comment's title line.

    Args:
        round_number: 1-based round number for this run.
        head_sha: Head commit sha reviewed in this round, possibly empty.
        verdict: Derived readiness verdict, including INCOMPLETE.

    Returns:
        The Markdown heading line with the verdict in the title.
    """
    del round_number, head_sha
    emoji = VERDICT_EMOJI[verdict]
    label = verdict_label(verdict=verdict)
    return f"## 🔎 Lintro Review — {emoji} {label}"


def _incomplete_banner(*, result: ReviewResult, verdict: ReviewVerdict) -> str:
    """Render the Variant B withheld-verdict callout.

    Args:
        result: Current review result, possibly with coverage counts.
        verdict: Derived verdict.

    Returns:
        A warning callout, or empty when the round is complete.
    """
    if verdict is not ReviewVerdict.INCOMPLETE or result.coverage is None:
        return ""
    counts = result.coverage
    cap = result.metadata.max_cost_usd
    cap_text = f"`${cap:.2f}`" if cap is not None else "the cost cap"
    awaiting = counts.awaiting
    eligible = counts.eligible
    reason_map = dict(result.awaiting_reasons)
    listed = result.awaiting_paths[:12]
    extra = max(len(result.awaiting_paths) - len(listed), 0)
    items: list[str] = []
    for path in listed:
        reason = reason_map.get(path, "")
        label = f"`{sanitize_comment_text(path, limit=200)}`"
        if reason:
            label = f"{label} — {sanitize_comment_text(reason, limit=160)}"
        items.append(label)
    if extra:
        items.append(f"*({extra} more)*")
    files_block = ""
    if items:
        files_block = (
            "\n>\n> <details><summary>"
            f"{awaiting} files awaiting review</summary>\n>\n> "
            + " · ".join(items)
            + "\n>\n> </details>"
        )
    return (
        "> [!WARNING]\n"
        f"> **Verdict withheld — {awaiting} of {eligible} files not yet "
        f"reviewed.** {cap_text} stopped this round after {counts.reviewed} "
        "files. The check stays ❌ until every file is covered at HEAD; "
        "the next round resumes with the unreviewed files first."
        f"{files_block}\n"
    )


def _coverage_section(*, result: ReviewResult, verdict: ReviewVerdict) -> str:
    """Render the Variant B coverage table.

    Args:
        result: Current review result.
        verdict: Derived verdict.

    Returns:
        The coverage table, or empty when the round is complete.
    """
    if verdict is not ReviewVerdict.INCOMPLETE or result.coverage is None:
        return ""
    counts = result.coverage
    return "\n".join(
        [
            "### Coverage this round",
            "",
            "| reviewed now | carried forward | awaiting review "
            "| invalidated (re-queued) |",
            "|:-:|:-:|:-:|:-:|",
            (
                f"| **{counts.reviewed}** | {counts.carried} | "
                f"**{counts.awaiting}** | {counts.invalidated} |"
            ),
        ],
    )


def _readiness_pill(
    *,
    verdict: ReviewVerdict,
    records: tuple[FindingRecord, ...],
) -> str:
    """Render the derived readiness verdict and what decided it.

    Args:
        verdict: Readiness verdict derived from the open findings.
        records: Every tracked finding record.

    Returns:
        A single bold line, for example ``**⛔ Blocked** — 1 open blocker``.
    """
    label = f"**{VERDICT_EMOJI[verdict]} {verdict_label(verdict=verdict)}**"
    if verdict is ReviewVerdict.READY:
        return f"{label} — no open findings"
    severity = _VERDICT_SEVERITY[verdict]
    count = sum(
        1
        for record in records
        if record.status is FindingStatus.OPEN
        and not record.is_question
        and record.severity is severity
    )
    noun = _plural(count=count, noun=_VERDICT_NOUNS[verdict])
    return f"{label} — {count} open {noun}"


def _verdict_explainer() -> str:
    """Render the verdict-derivation rubric as fine-print under the pill.

    It sits directly under the readiness verdict, not inside the reasoning
    section: the rubric explains the *pill*, and a reader who wants to know how
    "Blocked" was decided should not have to find it three sections later. It is
    rendered on every round, including a clean ``READY`` one — that is precisely
    the round where a reader most needs to know the verdict was derived from
    open findings rather than asked of the model.

    Returns:
        The ``<sub>``-wrapped rubric line.
    """
    return f"<sub>{VERDICT_RUBRIC_FINE_PRINT}</sub>"


def _delta_line(*, match: FindingMatchResult, round_number: int) -> str:
    """Render the round-over-round finding delta.

    Args:
        match: Cross-round matching outcome for this round.
        round_number: 1-based round number for this run.

    Returns:
        The delta line, or an empty string on round 1 where there is nothing
        to compare against.
    """
    if round_number <= 1:
        return ""
    parts = [
        f"✔ {len(match.resolved)} resolved",
        f"**{len(match.new)} new**",
    ]
    # A regressed finding was resolved and came back, so it is emphatically not
    # unchanged — and the open table already labels it "↩ regressed". Folding it
    # into the unchanged count made the two contradict each other.
    if match.regressed:
        parts.append(f"↩ {len(match.regressed)} regressed")
    parts.append(f"{len(match.carried)} unchanged since round {round_number - 1}")
    return " · ".join(parts)


def _summary_section(*, result: ReviewResult) -> str:
    """Render the one-line summary plus severity-marked walkthrough bullets.

    A bullet tied to an open P1/P2 finding carries that finding's severity dot
    and is bolded, so a blocker can never read as neutral prose in the
    walkthrough.

    Args:
        result: Current review result.

    Returns:
        The ``Summary`` section, or an empty string when nothing was returned.
    """
    summary = result.pr_summary
    headline = sanitize_comment_text(
        (summary.headline if summary else "") or result.summary or "",
        limit=1000,
    ).strip()
    bullets = summary.walkthrough if summary else ()
    if not headline and not bullets:
        return ""

    lines = ["### Summary"]
    if headline:
        lines.extend(["", headline])
    if bullets:
        lines.append("")
        lines.extend(
            _summary_bullet(
                text=bullet.text,
                finding_ref=bullet.finding_ref,
                result=result,
            )
            for bullet in bullets
        )
    return "\n".join(lines)


def _summary_bullet(*, text: str, finding_ref: str, result: ReviewResult) -> str:
    """Render one walkthrough bullet, severity-marked when it names a blocker.

    Args:
        text: Bullet text as written by the model.
        finding_ref: The bullet's ``file:line`` finding reference, possibly
            empty.
        result: Current review result, used to resolve the reference.

    Returns:
        The Markdown list item.
    """
    safe = sanitize_comment_text(text, limit=500).strip()
    finding = resolve_bullet_finding(
        finding_ref=finding_ref,
        findings=result.findings,
    )
    if finding is None or finding.severity not in {Severity.P1, Severity.P2}:
        return f"- {safe}"
    return f"- {_SEVERITY_EMOJI[finding.severity]} **{safe}**"


def _reasoning_section(*, result: ReviewResult, verdict: ReviewVerdict) -> str:
    """Render the model's verdict reasoning and the files it points at.

    The derivation rubric is deliberately *not* here: it explains the readiness
    pill and is rendered directly under it by :func:`_verdict_explainer`.

    Args:
        result: Current review result.
        verdict: Readiness verdict derived from the open findings.

    Returns:
        The reasoning section, or an empty string when there is nothing to
        explain (no reasoning and nothing open).
    """
    reasoning = result.verdict_reasoning
    has_reasoning = reasoning is not None and not reasoning.is_empty
    if not has_reasoning and verdict is ReviewVerdict.READY:
        return ""

    lines = [f"### {_REASONING_HEADINGS[verdict]}"]
    if reasoning is not None:
        for paragraph in (reasoning.deciding_factor, reasoning.failure_mechanism):
            text = sanitize_comment_text(paragraph, limit=2000).strip()
            if text:
                lines.extend(["", text])
    if reasoning is not None and reasoning.files_needing_attention:
        files = " · ".join(
            f"`{sanitize_comment_text(path, limit=200)}`"
            for path in reasoning.files_needing_attention
        )
        lines.extend(["", f"**Files needing attention:** {files}"])
    return "\n".join(lines)


def _tiles_section(*, records: tuple[FindingRecord, ...]) -> str:
    """Render the compact severity tiles, including the cumulative fixed count.

    Args:
        records: Every tracked finding record.

    Returns:
        A three-row Markdown table.
    """
    counts = dict.fromkeys((Severity.P1, Severity.P2, Severity.P3), 0)
    fixed = 0
    for record in records:
        # A question that stopped being asked was never a defect, so it must not
        # inflate the "fixed" tile, which reads as remediated findings.
        if record.is_question:
            continue
        if record.status is FindingStatus.RESOLVED:
            fixed += 1
            continue
        counts[record.severity] = counts.get(record.severity, 0) + 1
    return "\n".join(
        [
            "| 🔴 blockers | 🟠 warnings | 🟡 nits | ✔ fixed |",
            "|:-:|:-:|:-:|:-:|",
            (
                f"| **{counts[Severity.P1]}** | **{counts[Severity.P2]}** | "
                f"**{counts[Severity.P3]}** | **{fixed}** |"
            ),
        ],
    )


def _suggestion_drops_row(*, result: ReviewResult) -> str:
    """Render the warning row shown when patch validation dropped suggestions.

    A suggestion that no longer matches the file at head is withheld rather
    than posted (#2101). Withholding it silently would leave the reader
    believing the model simply had no mechanical fix, so the count and the
    reasons are stated on the sticky next to the other no-silent-caps notices.

    Args:
        result: Current review result.

    Returns:
        A blockquote warning naming the count and reasons, or an empty string
        when every suggestion validated.
    """
    notice = describe_suggestion_drops(findings=result.findings)
    if not notice:
        return ""
    return (
        f"> ✂️ **{sanitize_comment_text(notice, limit=300)}** — the described "
        "fix is kept on each finding; only the one-click commit is withheld."
    )


def _coverage_limited_row(*, result: ReviewResult) -> str:
    """Render the warning row shown when a findings cap limited this round.

    Sits with the other no-silent-caps notices (``_degraded_row``,
    ``_suggestion_drops_row``) and shares its text with the per-review body
    through :func:`format_coverage_limited_warning`, so the sticky can never
    present a capped round as an unmarked complete one (#2003).

    Args:
        result: Current review result.

    Returns:
        A blockquote warning, or an empty string when coverage was complete.
    """
    return format_coverage_limited_warning(metadata=result.metadata)


def _cross_chunk_row(*, result: ReviewResult) -> str:
    """Render the note shown when the cross-chunk guard downgraded findings.

    Sits with the other no-silent-edit notices (``_degraded_row``,
    ``_suggestion_drops_row``, ``_coverage_limited_row``) and shares its text
    with the per-review body through :func:`format_cross_chunk_note`, so the
    sticky can never present a guard-lowered severity as the model's own
    (#2265).

    Args:
        result: Current review result.

    Returns:
        A blockquote note, or an empty string when the guard did not fire.
    """
    return format_cross_chunk_note(findings=result.findings)


def _degraded_row(*, failure: InlinePostFailure | None) -> str:
    """Render the warning row shown when inline posting failed.

    Shares its text with the failure's ``reason`` through
    :func:`format_inline_post_note`, so the row can only ever name the cause
    GitHub actually reported (#2266).

    Args:
        failure: Findings whose inline comments could not be posted.

    Returns:
        A blockquote warning naming the count and cause, or an empty string
        when inline posting succeeded.
    """
    return format_inline_post_note(failure=failure)

"""One-line prose notes shared by the GitHub review comment surfaces.

Timings, synthesis, coverage degradation, inline-post failures, cross-chunk
contradictions, convergence and the run-mechanics footer all render as short
sentences appended to whichever body is being assembled. They are formatting,
not assembly: a surface imports the note it needs from here and decides where
it goes, while :mod:`lintro.ai.review.github_render` owns the assembly itself
and imports nothing from this module.
"""

from __future__ import annotations

from collections.abc import Sequence

from lintro.ai.resolved_ai_config import (
    MAX_COST_LABEL,
    format_max_cost_label,
    format_sourced_value,
)
from lintro.ai.review.convergence import (
    format_convergence_stamp,
    format_score,
    format_trajectory,
)
from lintro.ai.review.coverage_degradation import (
    COVERAGE_LIMITED_HEADLINE,
    describe_coverage_degradations,
)
from lintro.ai.review.enums.cross_chunk_contradiction import CrossChunkContradiction
from lintro.ai.review.enums.inline_post_failure_kind import InlinePostFailureKind
from lintro.ai.review.github_badges import (
    format_cost,
    format_int,
    format_tokens,
)
from lintro.ai.review.models.convergence_decision import ConvergenceDecision
from lintro.ai.review.models.inline_post_failure import InlinePostFailure
from lintro.ai.review.models.review_finding import ReviewFinding
from lintro.ai.review.models.review_metadata import ReviewMetadata
from lintro.ai.review.sanitize import sanitize_comment_text
from lintro.ai.review.severity_gate import describe_cross_chunk_contradictions
from lintro.ai.review.synthesis_note import format_synthesis_note
from lintro.ai.review.timings import format_timing_summary

__all__ = [
    "format_convergence_banner",
    "format_convergence_note",
    "format_coverage_limited_warning",
    "format_cross_chunk_note",
    "format_inline_post_cause",
    "format_inline_post_note",
    "format_run_mechanics",
    "format_synthesis_note_line",
    "format_timings_note",
    "sanitized_timing_summary",
]


def sanitized_timing_summary(*, metadata: ReviewMetadata) -> str:
    """Return the per-phase timing summary, sanitized for a posted comment.

    The single cap for every GitHub surface, so a later change cannot clip
    one comment and not another. The text is trusted instrumentation, not
    model prose: the cap only bounds a pathological run.

    Args:
        metadata: Review run metadata.

    Returns:
        The sanitized summary, or an empty string when the run was not
        instrumented.
    """
    if metadata.timings is None:
        return ""
    return sanitize_comment_text(
        format_timing_summary(timings=metadata.timings),
        limit=1000,
    )


def format_timings_note(*, metadata: ReviewMetadata) -> str:
    """Render the per-phase timing summary as a small note for posted comments.

    Shared by every success surface that shows run mechanics (the review
    body's run-stats block and the sticky's ``This run`` table) so the posted
    comment carries the same one-line breakdown the terminal prints (#2148).

    Args:
        metadata: Review run metadata.

    Returns:
        A ``<sub>`` line with the summary, or an empty string when the run was
        not instrumented.
    """
    summary = sanitized_timing_summary(metadata=metadata)
    return f"<sub>Timings: {summary}</sub>" if summary else ""


def format_synthesis_note_line(*, metadata: ReviewMetadata) -> str:
    """Render the cross-chunk synthesis note as a small note for comments.

    Shares its wording with the terminal through
    :func:`format_synthesis_note`, so the posted comment can never describe
    the extra pass differently from the run that produced it (#2269).

    Args:
        metadata: Review run metadata.

    Returns:
        A ``<sub>`` line describing the pass, or an empty string when the pass
        did not run.
    """
    note = format_synthesis_note(metadata=metadata)
    if not note:
        return ""
    return f"<sub>{sanitize_comment_text(note, limit=400)}</sub>"


def format_coverage_limited_warning(*, metadata: ReviewMetadata) -> str:
    """Render the shared coverage-limited warning for posted GitHub surfaces.

    The review body and the sticky comment both call this, so the two can
    never disagree about whether a run was capped (#2003). It is the sibling
    of the cost-cap ``partial`` warning and carries equal prominence: a capped
    run is *not* a guaranteed full finding set.

    Args:
        metadata: Review run metadata.

    Returns:
        A blockquote warning, or an empty string when coverage was complete.
    """
    detail = describe_coverage_degradations(metadata=metadata)
    if not detail:
        return ""
    return (
        f"> ⚠️ **{COVERAGE_LIMITED_HEADLINE}** — "
        f"{sanitize_comment_text(detail, limit=400)}"
    )


_INLINE_POST_CAUSES: dict[InlinePostFailureKind, str] = {
    InlinePostFailureKind.RATE_LIMITED: "GitHub rate limit",
    InlinePostFailureKind.LINE_MAPPING: (
        "some findings map to no line in this PR's diff"
    ),
    InlinePostFailureKind.PERMISSION: (
        "this token is not permitted to post reviews on this PR"
    ),
    InlinePostFailureKind.OTHER: "the inline review comments could not be posted",
}


def format_inline_post_cause(
    *,
    kind: InlinePostFailureKind,
    status: int | None = None,
) -> str:
    """Render the human cause for a failed or skipped inline post.

    The single source of the wording for every surface that explains why a
    finding has no inline comment: the sticky's degraded row, the reason
    stored on :class:`~lintro.ai.review.models.inline_post_failure.InlinePostFailure`,
    and the JSON payload the CI classifier reads (#2266).

    Args:
        kind: Classified cause of the failure.
        status: HTTP status GitHub answered with, named in the text when
            known.

    Returns:
        A short lowercase phrase, with ``(HTTP <status>)`` appended when a
        status is known.
    """
    cause = _INLINE_POST_CAUSES[kind]
    return f"{cause} (HTTP {status})" if status is not None else cause


def format_inline_post_note(*, failure: InlinePostFailure | None) -> str:
    """Render the warning row shown when findings have no inline comment.

    Args:
        failure: Findings whose inline comments could not be posted.

    Returns:
        A blockquote warning naming the count and the cause, or an empty
        string when every finding reached an inline comment.
    """
    if failure is None or failure.is_empty:
        return ""
    noun = "finding" if failure.count == 1 else "findings"
    surface = "an inline comment" if failure.count == 1 else "inline comments"
    reason = sanitize_comment_text(failure.reason, limit=200).strip()
    cause = f" ({reason})" if reason else ""
    return (
        f"> ⚠️ **{failure.count} {noun} could not be posted as {surface}**"
        f"{cause}. Full details are folded in below instead."
    )


def _cross_chunk_band_clause(*, findings: Sequence[ReviewFinding]) -> str:
    """Describe the severity effect of the tagged findings, if any moved.

    Args:
        findings: Findings after the cross-chunk guard ran.

    Returns:
        ``", one band lower"`` when at least one tagged finding was actually
        downgraded; an empty string when only P3 findings were tagged.
    """
    lowered = any(
        finding.cross_chunk_contradiction
        is CrossChunkContradiction.UNCHANGED_FILE_CLAIM_DOWNGRADED
        for finding in findings
    )
    return ", one band lower" if lowered else ""


def format_cross_chunk_note(*, findings: Sequence[ReviewFinding]) -> str:
    """Render the shared cross-chunk downgrade note for posted GitHub surfaces.

    The review body and the sticky comment both call this, so neither can
    describe the guard differently from the other (#2265). The note says what
    was downgraded and why, because the alternative — editing severities the
    model reported and saying nothing — is exactly the silent behavior the
    other no-silent-caps notices exist to prevent.

    Args:
        findings: Findings for the current round.

    Returns:
        A blockquote note naming the count, or an empty string when the guard
        did not fire.
    """
    notice = describe_cross_chunk_contradictions(findings=findings)
    if not notice:
        return ""
    return (
        f"> 🧩 **{sanitize_comment_text(notice, limit=300)}** — chunked review "
        "shows each chunk the other files at the base commit, so the claim is "
        f"chunk-local; the finding is kept"
        f"{_cross_chunk_band_clause(findings=findings)}."
    )


def format_convergence_note(*, trajectory: tuple[float, ...]) -> str:
    """Render the convergence score and its trajectory as a one-line note.

    The single builder for this line, so the mission-control sticky and any
    later surface that shows the stability signal can never disagree about
    how a trajectory reads (#2099). The latest score leads because that is
    the number the stop rule compares; the arrow chain behind it is what
    tells a reader whether the review is settling or still moving.

    Args:
        trajectory: Recorded scores, oldest first.

    Returns:
        A ``<sub>`` line, or an empty string when no round carries a score —
        which is every round persisted before scoring existed.
    """
    if not trajectory:
        return ""
    latest = format_score(score=trajectory[-1])
    if len(trajectory) == 1:
        return f"<sub>Convergence score {latest}</sub>"
    return (
        f"<sub>Convergence score {latest} · trajectory "
        f"{format_trajectory(scores=trajectory)}</sub>"
    )


def format_convergence_banner(
    *,
    decision: ConvergenceDecision,
    open_p1: int = 0,
) -> str:
    """Render the blockquote stamped on the sticky for a short-circuited round.

    Args:
        decision: The converged decision that skipped the round.
        open_p1: Open, non-question P1 findings the last real round left in
            force. Named on the banner when non-zero: the skip does not
            redden the CI check for them (a reviewed round does not either),
            so the board is where a reader has to be able to see that
            something is still outstanding.

    Returns:
        A blockquote naming the round, the score, the threshold, any P1
        findings still open, and how to force a round anyway.
    """
    noun = "finding" if open_p1 == 1 else "findings"
    remaining = (
        f" Skipped: {open_p1} open P1 {noun} remain from the last reviewed " "round."
        if open_p1 > 0
        else ""
    )
    return (
        f"> 🔁 **Converged** — {format_convergence_stamp(decision=decision)} "
        f"over {decision.stable_rounds} consecutive rounds. No provider call "
        f"was made this round.{remaining} Re-run with `--full` to review again."
    )


def format_run_mechanics(*, metadata: ReviewMetadata) -> str:
    """Format the per-run mechanics footer for a single review run.

    Args:
        metadata: Review run metadata.

    Returns:
        Markdown describing model, provider, tokens, cost, depth, duration,
        and (when instrumented) the per-phase timing breakdown. Estimated
        token/cost figures are prefixed with ``~``.
    """
    estimated = metadata.token_usage_estimated
    total_tokens = int(metadata.token_usage.get("total", 0))
    prompt_tokens = int(metadata.token_usage.get("prompt", 0))
    completion_tokens = int(metadata.token_usage.get("completion", 0))
    source = "estimated" if estimated else "provider-reported"
    parts = [
        "**Model:** "
        + format_sourced_value(
            f"`{sanitize_comment_text(metadata.model, limit=60)}`",
            metadata.model_source or None,
        ),
        "**Provider:** "
        + format_sourced_value(
            f"`{sanitize_comment_text(metadata.provider, limit=40)}`",
            metadata.provider_source or None,
        ),
    ]
    if metadata.transport or metadata.transport_source:
        parts.append(
            "**Transport:** "
            + format_sourced_value(
                f"`{sanitize_comment_text(metadata.transport or 'unset', limit=40)}`",
                metadata.transport_source or None,
            ),
        )
    if metadata.max_cost_usd is not None or metadata.max_cost_usd_source:
        parts.append(
            f"**{MAX_COST_LABEL}:** "
            + format_max_cost_label(
                max_cost_usd=metadata.max_cost_usd,
                source=metadata.max_cost_usd_source or None,
            ),
        )
    parts.extend(
        [
            f"**Depth:** {metadata.depth}",
            (
                f"**Tokens:** {format_tokens(total_tokens, estimated=estimated)} "
                f"(in {format_int(prompt_tokens)} / "
                f"out {format_int(completion_tokens)}, "
                f"{source})"
            ),
            f"**Est. cost:** "
            f"{format_cost(metadata.cost_estimate_usd, estimated=estimated)}",
            f"**Duration:** {metadata.duration_seconds:.1f}s",
        ],
    )
    timing_summary = sanitized_timing_summary(metadata=metadata)
    if timing_summary:
        # Per-phase breakdown for the run (#2148) on the error-sticky footer.
        parts.append(f"**Timings:** {timing_summary}")
    return " · ".join(parts)

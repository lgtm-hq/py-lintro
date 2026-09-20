"""Prompt construction and diff budgeting for the synthesis pass (#2269).

Split from :mod:`lintro.ai.review.synthesis` so the pass module stays about
running the extra call while the question of *what the model gets to see* —
which files, how much diff, and what happens when the whole PR does not fit —
lives on its own and can be tested without a provider.

The budget reused here is the same per-call diff-token budget the chunk calls
were planned against, and it is spent against the *whole* prompt rather than
the diff alone. The changed-file list is reserved first and never trimmed: the
prompt's "do not claim a file was never updated" rule is only sound while that
list is complete, so on a pull request with a very large file list the span can
exceed the budget by design. The digest and then the diff take whatever remains
(each with a floor of one token), which is what keeps the extra call from being
the prompt in a run that overruns the context window on diff volume.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from lintro.ai.prompts.review import (
    REVIEW_SYNTHESIS_SYSTEM_PROMPT,
    REVIEW_SYNTHESIS_USER_PROMPT_TEMPLATE,
    format_changed_files_for_prompt,
    format_chunk_summaries_for_prompt,
)
from lintro.ai.review.context.diff_parse import split_unified_diff_by_file
from lintro.ai.review.models.chunk_summary import ChunkSummary
from lintro.ai.review.prompt_redaction import redact_prompt_text
from lintro.ai.sanitize import make_boundary_marker
from lintro.ai.token_budget import estimate_tokens, truncate_to_budget

if TYPE_CHECKING:
    from lintro.ai.review.merge import ChunkReviewPartial
    from lintro.ai.review.models.review_chunk import ReviewChunk
    from lintro.ai.review.models.review_context import ReviewContext

__all__ = [
    "SynthesisPromptPlan",
    "build_synthesis_prompt",
    "chunk_summaries",
    "cross_chunk_paths",
    "guarded_changed_paths",
    "plan_synthesis_prompt",
    "select_synthesis_diff",
]


def guarded_changed_paths(*, context: ReviewContext) -> tuple[str, ...]:
    """Return every path the cross-chunk guard treats as changed by the PR.

    Current paths plus rename and copy sources: a chunk-local claim that a
    rename's old path was never touched contradicts the diff just as a claim
    about the new path does. This list is only for the guard; custom-agent
    scoping keys on post-rename paths, whose diff sections exist.

    The single implementation. It lives here rather than in the orchestrator
    because the dependency only runs one way — the orchestrator imports
    :mod:`lintro.ai.review.synthesis`, which imports this module, so the
    reverse import would close a cycle. ``orchestrator.guard_changed_paths``
    is a thin re-export of this function.

    Args:
        context: Collected review context.

    Returns:
        Changed paths and rename/copy sources, in changed-file order.
    """
    return tuple(
        path
        for file in context.changed_files
        for path in (file.path, file.previous_path)
        if path
    )


def _referenced_paths(*, summary: ChunkSummary) -> frozenset[str]:
    """Collect every path one chunk touched or pointed at.

    Args:
        summary: Digest for one chunk.

    Returns:
        The chunk's own files plus every path its findings referenced. A
        finding that points at a file the chunk did not review is exactly the
        cross-chunk edge this pass is looking for, so those paths count.
    """
    paths = set(summary.files)
    for finding in summary.findings:
        for occurrence in finding.all_occurrences:
            if occurrence.file:
                paths.add(occurrence.file)
    return frozenset(paths)


def cross_chunk_paths(*, summaries: Sequence[ChunkSummary]) -> tuple[str, ...]:
    """Rank the paths that more than one chunk referenced.

    Args:
        summaries: Per-chunk digests in chunk order.

    Returns:
        Paths referenced by two or more chunks, in sorted order. These are the
        seams the pass exists to inspect, so they are the first diffs kept
        when the whole PR does not fit the budget.
    """
    counts: dict[str, int] = {}
    for summary in summaries:
        for path in _referenced_paths(summary=summary):
            counts[path] = counts.get(path, 0) + 1
    return tuple(sorted(path for path, count in counts.items() if count > 1))


def select_synthesis_diff(
    *,
    context: ReviewContext,
    summaries: Sequence[ChunkSummary],
    diff_budget: int,
) -> tuple[str, bool]:
    """Choose the diff text the synthesis prompt embeds, within budget.

    Takes whatever the whole prompt's other spans left over, so the extra
    call cannot be the one prompt in the run that overruns the context
    window. When the whole PR fits, it is sent whole. When it does not, the
    files that more than one chunk referenced go in first — those are the
    seams the pass exists to inspect — and the remaining files follow in path
    order until the budget is spent.

    The first file that does not fit ends the selection. A cross-chunk file
    is cut into whatever budget is left and kept, because half a seam is
    still the seam; a non-priority one is simply dropped. Either way nothing
    follows it, so the model never reads a diff that jumps out of one file
    mid-hunk and straight into another.

    Args:
        context: Collected review diff context.
        summaries: Per-chunk digests in chunk order.
        diff_budget: Token budget available for embedded diff content.

    Returns:
        Tuple of ``(diff_text, truncated)``. ``truncated`` is True whenever
        any changed file's diff was left out or cut short, so the run can
        record that the pass saw less than the whole PR.
    """
    budget = max(diff_budget, 1)
    whole = context.unified_diff
    if estimate_tokens(whole) <= budget:
        return whole, False

    per_file = split_unified_diff_by_file(unified_diff=whole)
    if not per_file:
        # Nothing to rank by file, so fall back to a straight cut rather than
        # dropping the diff entirely and asking the model to guess.
        text, _cut = truncate_to_budget(whole, budget)
        return text, True

    priority = cross_chunk_paths(summaries=summaries)
    ordered = [path for path in priority if path in per_file]
    seen = set(ordered)
    ordered.extend(path for path in sorted(per_file) if path not in seen)

    priority_paths = set(priority)
    kept: list[str] = []
    spent = 0
    truncated = False
    for path in ordered:
        section = per_file[path]
        cost = estimate_tokens(section)
        if spent + cost <= budget:
            kept.append(section)
            spent += cost
            continue
        truncated = True
        remaining = budget - spent
        # A cross-chunk file is a seam this pass exists to inspect, so a cut
        # section still shows the model the change it must reason about; an
        # empty prompt shows it nothing. The same holds for the very first
        # file when even it overruns the whole budget on its own.
        if remaining > 0 and (path in priority_paths or not kept):
            text, _cut = truncate_to_budget(section, remaining)
            kept.append(text)
        # Never let a lower-priority file follow a cut one: the diff would
        # then jump from half a file straight into another, and the model
        # cannot tell where the gap is.
        break
    return "".join(kept), truncated


@dataclass(frozen=True, slots=True)
class SynthesisPromptPlan:
    """Every untrusted span of the synthesis prompt, already fitted to budget.

    Built in one place so the budget is spent against the whole prompt rather
    than the diff alone: the changed-file list and the per-chunk digest are
    rendered first and charged against the same ceiling, and only what they
    leave over is offered to the diff.

    Attributes:
        changed_files: Rendered whole-PR changed-file list. Never trimmed —
            the prompt's "do not claim a file was never updated" rule is only
            sound while this list is complete.
        chunk_digest: Rendered per-chunk digest, with finding lines dropped
            from the largest chunks first when it alone overruns the budget.
        diff: Diff text selected by :func:`select_synthesis_diff`.
        truncated: True when any span was cut or dropped, so the run can
            record that the pass saw less than the whole PR.
        diff_files_included: Changed files whose diff section reached the
            prompt whole (#2704). A cut section does not count, so
            ``included == total`` means only the finding digest was trimmed.
        diff_files_total: Changed files in the PR's diff.
    """

    changed_files: str
    chunk_digest: str
    diff: str
    truncated: bool
    diff_files_included: int = 0
    diff_files_total: int = 0


def _trim_chunk_digest(
    *,
    summaries: Sequence[ChunkSummary],
    budget: int,
    finding_ids: Mapping[tuple[str, int, str], str] | None = None,
) -> tuple[str, bool]:
    """Render the per-chunk digest, shedding finding lines until it fits.

    Which files each chunk reviewed is what makes the pass's answer
    attributable to a chunk boundary at all, so the per-chunk file lines are
    the last thing to go. The already-reported finding lines only suppress
    restatements, so they are shed first, and from the largest chunks first
    because those cost the most for the least marginal suppression.

    Args:
        summaries: Per-chunk digests in chunk order.
        budget: Token ceiling the rendered digest must fit.
        finding_ids: Digest id per merged finding, printed on its line.

    Returns:
        Tuple of ``(digest_text, truncated)``.
    """
    text = format_chunk_summaries_for_prompt(
        summaries=summaries,
        finding_ids=finding_ids,
    )
    if estimate_tokens(text) <= budget:
        return text, False

    trimmed = list(summaries)
    while any(summary.findings for summary in trimmed):
        widest = max(
            range(len(trimmed)),
            key=lambda index: (len(trimmed[index].findings), -index),
        )
        trimmed[widest] = replace(trimmed[widest], findings=())
        text = format_chunk_summaries_for_prompt(
            summaries=trimmed,
            finding_ids=finding_ids,
        )
        if estimate_tokens(text) <= budget:
            return text, True

    # Even the file lines alone overrun the budget. A cut digest still tells
    # the model which chunk boundary it is reasoning across; an empty one
    # tells it nothing.
    cut, _was_cut = truncate_to_budget(text, max(budget, 1))
    return cut, True


def plan_synthesis_prompt(
    *,
    context: ReviewContext,
    summaries: Sequence[ChunkSummary],
    diff_budget: int,
    finding_ids: Mapping[tuple[str, int, str], str] | None = None,
) -> SynthesisPromptPlan:
    """Fit the whole synthesis prompt — not only its diff — into one budget.

    The digest and the changed-file list are rendered first and their tokens
    reserved, so a run with many chunks and many already-reported findings
    shrinks the diff rather than silently overrunning the context window.

    Args:
        context: Collected review diff context.
        summaries: Per-chunk digests in chunk order.
        diff_budget: Token budget available for the prompt's untrusted spans.
        finding_ids: Digest id per merged finding (see
            :func:`~lintro.ai.review.synthesis_narrative.finding_ids`), so a
            duplicate group can name a finding unambiguously.

    Returns:
        The fitted plan. ``truncated`` is True when the digest was trimmed or
        the diff selection dropped or cut a file.
    """
    budget = max(diff_budget, 1)
    changed_files = format_changed_files_for_prompt(files=list(context.changed_files))
    digest_budget = max(budget - estimate_tokens(changed_files), 1)
    chunk_digest, digest_truncated = _trim_chunk_digest(
        summaries=summaries,
        budget=digest_budget,
        finding_ids=finding_ids,
    )
    reserve = estimate_tokens(changed_files) + estimate_tokens(chunk_digest)
    diff, diff_truncated = select_synthesis_diff(
        context=context,
        summaries=summaries,
        diff_budget=max(budget - reserve, 1),
    )
    return SynthesisPromptPlan(
        changed_files=changed_files,
        chunk_digest=chunk_digest,
        diff=diff,
        truncated=digest_truncated or diff_truncated,
        diff_files_included=_count_whole_files(
            kept=diff,
            unified_diff=context.unified_diff,
        ),
        diff_files_total=len(
            split_unified_diff_by_file(unified_diff=context.unified_diff),
        ),
    )


def _count_whole_files(*, kept: str, unified_diff: str) -> int:
    """Return how many of the PR's per-file sections the fitted diff kept whole.

    Args:
        kept: The diff text selected for the prompt, possibly cut.
        unified_diff: The PR's whole unified diff.

    Returns:
        The number of files whose complete section appears in *kept*. A
        section the budget cut short is not counted, so the count equals the
        total only when no file's diff was dropped or cut (#2704).
    """
    if not kept:
        return 0
    sections = split_unified_diff_by_file(unified_diff=unified_diff)
    return sum(1 for section in sections.values() if section and section in kept)


def build_synthesis_prompt(
    *,
    context: ReviewContext,
    plan: SynthesisPromptPlan,
    max_findings: int,
) -> tuple[str, str]:
    """Build the system and user prompts for the synthesis pass.

    Every untrusted span — PR title and body, the changed-file list, the
    per-chunk digest, the diff — goes through :func:`redact_prompt_text`, so
    the extra call sits behind the same secret-redaction choke point as every
    chunk call.

    The system prompt is the pass's own, not the chunk system prompt: this
    call is asked for findings only and is never given a checklist, so a
    system prompt that mandates one would demand output the user template
    forbids.

    Args:
        context: Collected review diff context.
        plan: Prompt spans already fitted to the pass's token budget.
        max_findings: Ceiling written into the prompt's output rules.

    Returns:
        Tuple of ``(system_prompt, user_prompt)``.
    """
    pr_title = context.pr_metadata.title if context.pr_metadata else "Local changes"
    pr_summary = context.pr_metadata.body if context.pr_metadata else "(no PR summary)"
    truncation_note = (
        "\nNote: the diff below is only part of this PR — it was cut to fit a "
        "token budget. The changed-file list above is complete; the diff is "
        "not. Say nothing about a file whose diff you cannot see.\n"
        if plan.truncated
        else ""
    )
    user_prompt = REVIEW_SYNTHESIS_USER_PROMPT_TEMPLATE.format(
        pr_title=redact_prompt_text(text=pr_title, source="PR title"),
        pr_summary=redact_prompt_text(text=pr_summary, source="PR metadata"),
        boundary=make_boundary_marker(),
        changed_file_count=len(context.changed_files),
        changed_files=redact_prompt_text(
            text=plan.changed_files,
            source="changed files",
        ),
        chunk_summaries=redact_prompt_text(
            text=plan.chunk_digest,
            source="chunk summaries",
        ),
        truncation_note=truncation_note,
        diff=redact_prompt_text(text=plan.diff, source="diff"),
        max_findings=max_findings,
    )
    return REVIEW_SYNTHESIS_SYSTEM_PROMPT, user_prompt


def chunk_summaries(
    *,
    chunks: list[ReviewChunk],
    partials: list[ChunkReviewPartial],
) -> tuple[ChunkSummary, ...]:
    """Build the per-chunk digest the cross-chunk synthesis pass reads.

    Args:
        chunks: Chunks planned for this run, in plan order.
        partials: Completed chunk partials, in completion order.

    Returns:
        One digest per completed chunk. The chunk id is recovered from the
        plan by file set so the digest names the same chunk the reader sees
        elsewhere; a partial that matches no planned chunk falls back to its
        position, which keeps the digest readable rather than blank.
    """
    ids = {tuple(chunk.files): chunk.id for chunk in chunks}
    return tuple(
        ChunkSummary(
            chunk_id=ids.get(tuple(item.files), position),
            files=tuple(item.files),
            findings=item.findings,
        )
        for position, item in enumerate(partials, start=1)
    )

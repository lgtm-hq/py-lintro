"""Prompt construction and diff budgeting for the synthesis pass (#2269).

Split from :mod:`lintro.ai.review.synthesis` so the pass module stays about
running the extra call while the question of *what the model gets to see* —
which files, how much diff, and what happens when the whole PR does not fit —
lives on its own and can be tested without a provider.

The budget reused here is the same per-call diff-token budget the chunk calls
were planned against, so the extra call can never be the one prompt in a run
that overruns the context window.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from lintro.ai.prompts.review import (
    REVIEW_SYNTHESIS_USER_PROMPT_TEMPLATE,
    REVIEW_SYSTEM,
    format_changed_files_for_prompt,
    format_chunk_summaries_for_prompt,
)
from lintro.ai.review.context.diff_parse import split_unified_diff_by_file
from lintro.ai.review.prompt_redaction import redact_prompt_text
from lintro.ai.sanitize import make_boundary_marker
from lintro.ai.token_budget import estimate_tokens, truncate_to_budget

if TYPE_CHECKING:
    from lintro.ai.review.models.chunk_summary import ChunkSummary
    from lintro.ai.review.models.review_context import ReviewContext

__all__ = [
    "build_synthesis_prompt",
    "cross_chunk_paths",
    "select_synthesis_diff",
]


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

    Reuses the same per-call diff-token budget the chunk calls were planned
    against, so the extra call cannot be the one prompt in the run that
    overruns the context window. When the whole PR fits, it is sent whole.
    When it does not, the files that more than one chunk referenced go in
    first — those are the seams the pass exists to inspect — and the remaining
    files follow in path order until the budget is spent.

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
    ordered.extend(path for path in sorted(per_file) if path not in set(ordered))

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
        if not kept:
            # Even the highest-priority file overruns the budget on its own.
            # A cut section still shows the model the change it must reason
            # about; an empty prompt shows it nothing.
            text, _cut = truncate_to_budget(section, budget)
            kept.append(text)
            spent = budget
    return "".join(kept), truncated


def build_synthesis_prompt(
    *,
    context: ReviewContext,
    summaries: Sequence[ChunkSummary],
    diff: str,
    truncated: bool,
    max_findings: int,
) -> tuple[str, str]:
    """Build the system and user prompts for the synthesis pass.

    Every untrusted span — PR title and body, the changed-file list, the
    per-chunk digest, the diff — goes through :func:`redact_prompt_text`, so
    the extra call sits behind the same secret-redaction choke point as every
    chunk call.

    Args:
        context: Collected review diff context.
        summaries: Per-chunk digests in chunk order.
        diff: Diff text selected by :func:`select_synthesis_diff`.
        truncated: Whether that selection dropped or cut any file.
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
        if truncated
        else ""
    )
    user_prompt = REVIEW_SYNTHESIS_USER_PROMPT_TEMPLATE.format(
        pr_title=redact_prompt_text(text=pr_title, source="PR title"),
        pr_summary=redact_prompt_text(text=pr_summary, source="PR metadata"),
        boundary=make_boundary_marker(),
        changed_file_count=len(context.changed_files),
        changed_files=redact_prompt_text(
            text=format_changed_files_for_prompt(files=list(context.changed_files)),
            source="changed files",
        ),
        chunk_summaries=redact_prompt_text(
            text=format_chunk_summaries_for_prompt(summaries=summaries),
            source="chunk summaries",
        ),
        truncation_note=truncation_note,
        diff=redact_prompt_text(text=diff, source="diff"),
        max_findings=max_findings,
    )
    return REVIEW_SYSTEM, user_prompt

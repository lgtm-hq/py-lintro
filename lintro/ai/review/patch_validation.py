"""Validate suggested patches against head file contents (#2101).

A GitHub ``suggestion`` block is a one-click commit. Posting one whose anchor
no longer matches the file is not a cosmetic miss: applied, it overwrites lines
the model never looked at. Model-named line numbers drift for ordinary reasons
— semantic chunking, multi-round runs where head moved, plain hallucination —
so the block is checked against the real file at head before anything renders
it, in the same mechanical spirit as the P1 evidence gate in
:mod:`lintro.ai.review.severity_gate`.

The check has exactly three outcomes:

* the named lines still hold the change's ``before`` block — the suggestion
  passes untouched;
* the block sits elsewhere in the file at a **single** position — the change is
  re-anchored to it once, and the finding's own line moves with it;
* anything else — the suggestion is stripped, the finding's prose kept, and the
  reason recorded on :attr:`ReviewFinding.suggestion_dropped` so the drop is
  visible on every surface instead of being a silent cap.

Head content arrives as an injected callable (see
``lintro.ai.review.context.make_head_file_reader``) so the module stays pure
and testable without git or ``gh``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from pathlib import PurePosixPath

from loguru import logger

from lintro.ai.review.enums.suggestion_drop_reason import SuggestionDropReason
from lintro.ai.review.inline_fix import finding_suggested_change, normalize_diff_path
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.models.review_finding import ReviewFinding
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.suggested_change import SuggestedChange

__all__ = [
    "AnchorResolution",
    "HeadFileReader",
    "count_dropped_suggestions",
    "describe_suggestion_drops",
    "drop_reason_counts",
    "dropped_suggestion_findings",
    "resolve_anchor",
    "validate_result_suggested_patches",
    "validate_suggested_patches",
]

#: Reads a repository-relative path at the review head, returning ``None`` when
#: the path is unreadable there.
HeadFileReader = Callable[[str], str | None]


@dataclass(frozen=True, slots=True)
class AnchorResolution:
    """Outcome of checking one suggested change against head content.

    Attributes:
        change: The change to keep, already re-anchored when the block moved.
            ``None`` when the suggestion must be dropped.
        reason: Why the suggestion was dropped, or ``None`` when it survived.
        line_delta: Number of lines the change moved during re-anchoring. Zero
            when the anchor was exact; the finding's own line is shifted by the
            same amount so the comment stays on the code it describes.
    """

    change: SuggestedChange | None = None
    reason: SuggestionDropReason | None = None
    line_delta: int = 0


def _dropped(*, reason: SuggestionDropReason) -> AnchorResolution:
    """Build a drop resolution carrying its reason.

    Args:
        reason: Why the suggestion cannot be posted.

    Returns:
        The drop resolution.
    """
    return AnchorResolution(reason=reason)


def _find_block(
    *,
    lines: Sequence[str],
    block: Sequence[str],
) -> list[int]:
    """Return every 0-based index at which ``block`` occurs exactly in ``lines``.

    Only exact, whole-line matches count. A fuzzy match would let a suggestion
    land on code that merely resembles what the model read, which is the very
    failure this module exists to prevent.

    Args:
        lines: File content at head, split into lines.
        block: The before-block to locate, split into lines.

    Returns:
        Match start indices, in file order. Empty when the block never occurs.
    """
    span = len(block)
    if not span or span > len(lines):
        return []
    return [
        index
        for index in range(len(lines) - span + 1)
        if list(lines[index : index + span]) == list(block)
    ]


def resolve_anchor(
    *,
    content: str,
    change: SuggestedChange,
) -> AnchorResolution:
    """Check one suggested change against a file's content at head.

    Args:
        content: Full file content at the head revision.
        change: The change the finding proposes.

    Returns:
        The resolution: the change to keep (re-anchored if it drifted), or a
        drop reason. A change carrying no ``before`` block can only be checked
        for the existence of its line range — there is nothing to compare
        against and nothing to search for, so no re-anchor is attempted.
    """
    lines = content.splitlines()
    span_exists = 1 <= change.start_line <= change.end_line <= len(lines)
    block = change.before.splitlines()
    if not block:
        if span_exists:
            return AnchorResolution(change=change)
        return _dropped(reason=SuggestionDropReason.STALE_ANCHOR)

    if span_exists and lines[change.start_line - 1 : change.end_line] == block:
        return AnchorResolution(change=change)

    matches = _find_block(lines=lines, block=block)
    if not matches:
        return _dropped(reason=SuggestionDropReason.STALE_ANCHOR)
    if len(matches) > 1:
        return _dropped(reason=SuggestionDropReason.AMBIGUOUS_ANCHOR)

    start_line = matches[0] + 1
    return AnchorResolution(
        change=replace(
            change,
            start_line=start_line,
            end_line=start_line + len(block) - 1,
        ),
        line_delta=start_line - change.start_line,
    )


def _drop_suggestion(
    *,
    finding: ReviewFinding,
    reason: SuggestionDropReason,
) -> ReviewFinding:
    """Strip a finding's suggestion, keeping its prose and recording why.

    Both suggestion carriers are cleared: leaving ``suggested_code`` behind
    would let :func:`lintro.ai.review.inline_fix.finding_suggested_change`
    rebuild the very block validation just rejected.

    Args:
        finding: Finding whose suggestion failed validation.
        reason: Why the suggestion was dropped.

    Returns:
        The finding without a suggestion, tagged with the drop reason.
    """
    logger.info(
        "Dropping suggested patch for {file}:{line} ({title!r}): {reason}.",
        file=finding.file,
        line=finding.line,
        title=finding.title,
        reason=reason.value,
    )
    return replace(
        finding,
        suggested_change=None,
        suggested_code="",
        suggestion_dropped=reason,
    )


def _is_repo_relative(path: str) -> bool:
    """Return True when ``path`` stays inside the repository.

    Finding paths are model-authored, so a suggestion anchored at an absolute
    path or one that climbs out with ``..`` is refused before any reader sees
    it; the head readers confine themselves too, but the validator should not
    depend on it.

    Args:
        path: Normalized repository-relative path.

    Returns:
        True for a plain relative path with no parent-directory hops.
    """
    posix = PurePosixPath(path)
    return not posix.is_absolute() and ".." not in posix.parts


def _validate_one(
    *,
    finding: ReviewFinding,
    read_head_file: HeadFileReader,
) -> ReviewFinding:
    """Validate a single finding's suggested patch.

    Args:
        finding: Finding to validate.
        read_head_file: Reader for file content at the review head.

    Returns:
        The finding unchanged, re-anchored, or stripped of its suggestion.
    """
    change = finding_suggested_change(finding=finding)
    if change is None:
        return finding
    path = normalize_diff_path(finding.file)
    if not path or not _is_repo_relative(path):
        return _drop_suggestion(
            finding=finding,
            reason=SuggestionDropReason.FILE_MISSING,
        )
    content = read_head_file(path)
    if content is None:
        return _drop_suggestion(
            finding=finding,
            reason=SuggestionDropReason.FILE_MISSING,
        )
    resolution = resolve_anchor(content=content, change=change)
    if resolution.change is None:
        # ``reason`` is always set when no change survives; the fallback keeps
        # the drop honest rather than letting an unvalidated block through.
        return _drop_suggestion(
            finding=finding,
            reason=resolution.reason or SuggestionDropReason.STALE_ANCHOR,
        )
    if not resolution.line_delta:
        return finding
    line = finding.line + resolution.line_delta
    # ``suggested_code`` is kept in step with the re-anchored change: the MCP
    # payload serializes only that field, so clearing it would hide a patch
    # that just passed validation.
    return replace(
        finding,
        line=max(line, 1),
        suggested_change=resolution.change,
        suggested_code=resolution.change.replacement,
    )


def validate_suggested_patches(
    *,
    findings: Sequence[ReviewFinding],
    read_head_file: HeadFileReader,
) -> tuple[ReviewFinding, ...]:
    """Validate every finding's suggested patch against the file at head.

    Args:
        findings: Findings as parsed from the model, in payload order.
        read_head_file: Reader for file content at the review head.

    Returns:
        The same findings in the same order. Nothing is ever removed: a finding
        whose patch failed keeps its prose and gains a
        :attr:`ReviewFinding.suggestion_dropped` tag.
    """
    return tuple(
        _validate_one(finding=finding, read_head_file=read_head_file)
        for finding in findings
    )


def validate_result_suggested_patches(
    *,
    result: ReviewResult,
    context: ReviewContext,
) -> ReviewResult:
    """Validate every suggested patch in a review result against head.

    Shared by the CLI and MCP surfaces so both run the identical pass between
    parse and post: files are read at the context's head ref through a
    memoized reader, and findings are only ever stripped and tagged, never
    removed.

    Args:
        result: Review result carrying the parsed findings.
        context: Review context naming the head ref the files are read at.

    Returns:
        The result with validated findings, or the same result when it has
        no findings to check.
    """
    if not result.findings:
        return result
    # Resolved at call time, like the CLI and MCP entry points do for the rest
    # of the context layer: the package's lazy-export machinery can reload
    # ``context.collection``, and a module-level binding would keep calling
    # the stale copy.
    from lintro.ai.review.context.collection import make_head_file_reader

    return replace(
        result,
        findings=validate_suggested_patches(
            findings=result.findings,
            read_head_file=make_head_file_reader(context=context),
        ),
    )


def dropped_suggestion_findings(
    *,
    findings: Iterable[ReviewFinding],
) -> tuple[ReviewFinding, ...]:
    """Select the findings whose suggestion validation dropped.

    Args:
        findings: Findings to filter.

    Returns:
        The findings carrying a drop reason, in the order given.
    """
    return tuple(
        finding for finding in findings if finding.suggestion_dropped is not None
    )


def count_dropped_suggestions(*, findings: Iterable[ReviewFinding]) -> int:
    """Count the suggestions patch validation dropped.

    Args:
        findings: Findings to count over.

    Returns:
        Number of findings whose suggestion was stripped.
    """
    return len(dropped_suggestion_findings(findings=findings))


def drop_reason_counts(
    *,
    findings: Iterable[ReviewFinding],
) -> dict[str, int]:
    """Tally dropped suggestions by reason.

    Args:
        findings: Findings to tally over.

    Returns:
        Mapping of reason value to count, ordered by first appearance. Empty
        when nothing was dropped.
    """
    counts: dict[str, int] = {}
    for finding in dropped_suggestion_findings(findings=findings):
        reason = str(finding.suggestion_dropped)
        counts[reason] = counts.get(reason, 0) + 1
    return counts


def describe_suggestion_drops(*, findings: Iterable[ReviewFinding]) -> str:
    """Build the one-line drop notice surfaces render.

    Args:
        findings: Findings to summarize.

    Returns:
        A line such as ``"2 suggestions dropped as unsafe to commit:
        stale_anchor 1, ambiguous_anchor 1"``, or an empty string when nothing
        was dropped.
    """
    counts = drop_reason_counts(findings=findings)
    total = sum(counts.values())
    if not total:
        return ""
    noun = "suggestion" if total == 1 else "suggestions"
    detail = ", ".join(f"{reason} {count}" for reason, count in counts.items())
    return f"{total} {noun} dropped as unsafe to commit: {detail}"

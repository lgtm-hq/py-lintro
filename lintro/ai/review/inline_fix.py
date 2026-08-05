"""Fix-slot mode selection for inline finding comments (#1911).

An inline finding comment renders exactly one fix affordance. Mode A is a
committable GitHub ``suggestion`` block; mode B is a ``**Fix:**`` one-liner.
Mode A is only *valid* under conditions GitHub does not check for us:

* the comment must be anchored to lines changed in **this round's posted
  diff** — not merely somewhere in the PR's cumulative diff, so a finding
  carried over from an earlier round does not qualify; and
* the block must replace **exactly** the anchored lines, so the finding has to
  name a line range and its own line has to sit inside it.

Every rejection is a named :class:`SuggestionRejection` so the fallback to mode
B is explainable rather than mysterious.
"""

from __future__ import annotations

from dataclasses import dataclass

from lintro.ai.review.enums.fix_mode import FixMode
from lintro.ai.review.enums.suggestion_rejection import SuggestionRejection
from lintro.ai.review.models.review_finding import ReviewFinding
from lintro.ai.review.models.suggested_change import SuggestedChange

__all__ = [
    "MAX_REPLACED_LINES",
    "MAX_REPLACEMENT_CHARS",
    "InlineFixPlan",
    "finding_suggested_change",
    "normalize_diff_path",
    "plan_inline_fix",
]

#: Largest replacement rendered as a committable suggestion. The block is
#: repeated in the prompt panel, so an oversized change would push the comment
#: toward GitHub's 65,536-character limit and cost the reader the reasoning
#: too — a described fix is the safer trade.
MAX_REPLACEMENT_CHARS = 4_000

#: Largest line span one committable suggestion may replace. The range comes
#: from untrusted model output and is expanded into a set on the posting path,
#: so it is bounded before anything materializes it.
MAX_REPLACED_LINES = 200


def normalize_diff_path(path: str) -> str:
    r"""Normalize a finding path to the form the GitHub diff API reports.

    Args:
        path: Repository-relative path as reported by the model.

    Returns:
        The path with backslashes converted to forward slashes and a leading
        ``./`` removed; empty when nothing usable remains. Order matters: a
        Windows-style ``.\\src\\a.py`` only grows a strippable ``./`` after the
        separators are normalized, and surrounding whitespace has to go first
        or it hides the prefix from ``removeprefix``.
    """
    return path.strip().replace("\\", "/").removeprefix("./")


@dataclass(frozen=True, slots=True)
class InlineFixPlan:
    """The fix slot chosen for one inline finding comment.

    Attributes:
        mode: Which affordance to render.
        change: The validated change when ``mode`` is
            :attr:`FixMode.SUGGESTION`, else ``None``.
        rejection: Why mode A was rejected, or ``None`` when it was chosen.
    """

    mode: FixMode
    change: SuggestedChange | None = None
    rejection: SuggestionRejection | None = None

    @property
    def committable_change(self) -> SuggestedChange | None:
        """Return the change to render as a suggestion block, if any.

        Returns:
            The validated change in mode A, else ``None``. Callers branch on
            this rather than on :attr:`mode` so a mode without a change cannot
            reach the renderer as a half-built suggestion.
        """
        if self.mode is not FixMode.SUGGESTION:
            return None
        return self.change


def _described(*, rejection: SuggestionRejection) -> InlineFixPlan:
    """Build a mode B plan carrying the reason mode A was rejected.

    Args:
        rejection: Why the committable suggestion was not offered.

    Returns:
        The mode B plan.
    """
    return InlineFixPlan(mode=FixMode.DESCRIBED, rejection=rejection)


def finding_suggested_change(*, finding: ReviewFinding) -> SuggestedChange | None:
    """Return the structured change a finding proposes, if any.

    ``suggested_change`` (#1911) is preferred. A finding that only carries the
    older unranged ``suggested_code`` is treated as replacing its own single
    line, which is what that field has always meant — otherwise no model output
    predating the new field could ever produce a committable suggestion.

    Args:
        finding: Finding to read the proposed change from.

    Returns:
        The change, or ``None`` when the finding proposes no replacement text.
    """
    if finding.suggested_change is not None:
        return finding.suggested_change
    if finding.suggested_code:
        return SuggestedChange(
            start_line=finding.line,
            end_line=finding.line,
            replacement=finding.suggested_code,
        )
    return None


def plan_inline_fix(
    *,
    finding: ReviewFinding,
    round_diff_lines: dict[str, set[int]] | None,
    carried_over: bool = False,
) -> InlineFixPlan:
    """Choose the fix slot for a finding's inline comment.

    Args:
        finding: Finding the inline comment is anchored to.
        round_diff_lines: Lines changed by **this round's** posted diff, keyed
            by repository-relative path. ``None`` when the round's diff could
            not be determined, which rejects every suggestion.
        carried_over: True when this finding was already reported in an earlier
            round. Its thread predates this round's diff, so a suggestion on it
            is not committable.

    Returns:
        The chosen plan. Mode A is only returned when every validity condition
        holds; otherwise mode B, with the deciding rejection recorded.
    """
    change = finding_suggested_change(finding=finding)
    if change is None:
        return _described(rejection=SuggestionRejection.NO_SUGGESTED_CHANGE)
    if not change.replacement.strip():
        return _described(rejection=SuggestionRejection.EMPTY_REPLACEMENT)
    if len(change.replacement) > MAX_REPLACEMENT_CHARS:
        return _described(rejection=SuggestionRejection.REPLACEMENT_TOO_LARGE)
    if change.start_line < 1 or change.end_line < change.start_line:
        return _described(rejection=SuggestionRejection.INVALID_RANGE)
    if change.end_line - change.start_line + 1 > MAX_REPLACED_LINES:
        return _described(rejection=SuggestionRejection.SPAN_TOO_LARGE)
    if finding.line not in change.line_span:
        return _described(rejection=SuggestionRejection.ANCHOR_OUTSIDE_RANGE)
    if carried_over:
        return _described(rejection=SuggestionRejection.CARRIED_OVER)
    if round_diff_lines is None:
        return _described(rejection=SuggestionRejection.NO_ROUND_DIFF)
    changed = round_diff_lines.get(normalize_diff_path(finding.file), set())
    if not changed.issuperset(change.line_span):
        return _described(rejection=SuggestionRejection.LINES_NOT_IN_ROUND_DIFF)
    return InlineFixPlan(mode=FixMode.SUGGESTION, change=change)

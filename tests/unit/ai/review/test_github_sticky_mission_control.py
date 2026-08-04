"""Layout tests for the v5 "mission control" sticky comment (#1909)."""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

import pytest
from assertpy import assert_that

from lintro.ai.review.enums.checklist_display import ChecklistDisplay
from lintro.ai.review.enums.finding_kind import FindingKind
from lintro.ai.review.github_constants import (
    GITHUB_COMMENT_HARD_LIMIT,
    STICKY_MARKER,
)
from lintro.ai.review.github_sticky import (
    build_sticky_comment,
    parse_review_state_v2,
)
from lintro.ai.review.models.inline_post_failure import InlinePostFailure
from lintro.ai.review.models.review_finding import ReviewFinding, Severity
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.review_summary import ReviewSummary
from lintro.ai.review.models.run_record import RunRecord
from lintro.ai.review.models.summary_bullet import SummaryBullet
from lintro.ai.review.models.verdict_reasoning import VerdictReasoning

_DETAILS_TAG_RE = re.compile(r"</?details\b")
_ROUND_RE = re.compile(r"\*\*Round (\d+)\*\*")

#: Number of synthetic prior rounds used by the history-pruning sweep.
_PRIOR_ROUNDS = 8


def _finding(
    *,
    title: str,
    severity: Severity = Severity.P1,
    file: str = "src/example.py",
    line: int = 10,
    category: str = "security",
    kind: FindingKind = FindingKind.FINDING,
) -> ReviewFinding:
    """Build a review finding for sticky layout tests."""
    return ReviewFinding(
        severity=severity,
        category=category,
        file=file,
        line=line,
        title=title,
        description="Stores an application password as a module-level literal.",
        cause="Assigned at module scope.",
        fix="Read it from the environment.",
        confidence="high",
        kind=kind,
    )


def _with(
    *,
    base: ReviewResult,
    findings: tuple[ReviewFinding, ...],
    **overrides: Any,
) -> ReviewResult:
    """Return a copy of ``base`` carrying different findings and fields."""
    return replace(base, findings=findings, **overrides)


def _body_only(*, body: str) -> str:
    """Strip the hidden state blob so assertions only see rendered Markdown."""
    return body.split("<!-- lintro-ai-review-state", 1)[0]


def _max_details_depth(*, body: str) -> int:
    """Return the deepest ``<details>`` nesting level in a rendered body."""
    depth = 0
    deepest = 0
    for tag in _DETAILS_TAG_RE.findall(body):
        depth += 1 if tag == "<details" else -1
        deepest = max(deepest, depth)
    return deepest


# --- readiness pill ----------------------------------------------------------


@pytest.mark.parametrize(
    ("severity", "expected"),
    [
        (Severity.P1, "**⛔ Blocked** — 1 open blocker"),
        (Severity.P2, "**⚠️ Changes requested** — 1 open warning"),
        (Severity.P3, "**🟡 Nits only** — 1 open nit"),
    ],
)
def test_readiness_pill_is_derived_from_open_severities(
    sample_review_result: ReviewResult,
    severity: Severity,
    expected: str,
) -> None:
    """The pill label follows the highest open severity, never the model."""
    body = build_sticky_comment(
        result=_with(
            base=sample_review_result,
            findings=(_finding(title="Leak", severity=severity),),
        ),
    )

    assert_that(_body_only(body=body)).contains(expected)


def test_readiness_pill_reads_ready_with_nothing_open(
    sample_review_result: ReviewResult,
) -> None:
    """A round with no findings renders the ready pill and an empty index."""
    body = _body_only(
        body=build_sticky_comment(result=_with(base=sample_review_result, findings=())),
    )

    assert_that(body).contains("**✅ Ready** — no open findings")
    assert_that(body).contains("### Open findings (0)")
    assert_that(body).contains("✅ Nothing open.")


def test_readiness_pill_ignores_questions(
    sample_review_result: ReviewResult,
) -> None:
    """A question carries no severity, so it cannot block the PR."""
    body = _body_only(
        body=build_sticky_comment(
            result=_with(
                base=sample_review_result,
                findings=(
                    _finding(
                        title="Is this intentional?",
                        severity=Severity.P1,
                        kind=FindingKind.QUESTION,
                    ),
                ),
            ),
        ),
    )

    assert_that(body).contains("**✅ Ready** — no open findings")
    assert_that(body).contains("❓ question")


def test_pill_counts_only_the_deciding_severity(
    sample_review_result: ReviewResult,
) -> None:
    """Two open blockers alongside a nit read as two blockers, not three."""
    body = _body_only(
        body=build_sticky_comment(
            result=_with(
                base=sample_review_result,
                findings=(
                    _finding(title="Leak one", line=10),
                    _finding(title="Leak two", line=20),
                    _finding(title="Stale name", severity=Severity.P3, line=30),
                ),
            ),
        ),
    )

    assert_that(body).contains("**⛔ Blocked** — 2 open blockers")


# --- delta line --------------------------------------------------------------


def test_round_one_renders_no_delta_line(
    sample_review_result: ReviewResult,
) -> None:
    """There is nothing to compare against on the first round."""
    body = _body_only(body=build_sticky_comment(result=sample_review_result))

    assert_that(body).does_not_contain("resolved ·")
    assert_that(body).contains("round 1")


def test_delta_line_counts_resolved_new_and_unchanged(
    sample_review_result: ReviewResult,
) -> None:
    """Round two reports what changed since round one."""
    first = build_sticky_comment(
        result=_with(
            base=sample_review_result,
            findings=(
                _finding(title="Leak", line=10),
                _finding(title="Slow loop", severity=Severity.P2, line=44),
            ),
        ),
        head_sha="sha1",
    )

    second = build_sticky_comment(
        result=_with(
            base=sample_review_result,
            findings=(
                _finding(title="Leak", line=12),
                _finding(title="Unguarded divide", severity=Severity.P2, line=8),
            ),
        ),
        prior_state=parse_review_state_v2(body=first),
        head_sha="sha2",
    )

    assert_that(_body_only(body=second)).contains(
        "✔ 1 resolved · **1 new** · 1 unchanged since round 1",
    )


def test_open_table_marks_new_and_carries_since_round(
    sample_review_result: ReviewResult,
) -> None:
    """The Δ column separates a newly raised finding from a carried one."""
    first = build_sticky_comment(
        result=_with(base=sample_review_result, findings=(_finding(title="Leak"),)),
        head_sha="sha1",
    )

    second = _body_only(
        body=build_sticky_comment(
            result=_with(
                base=sample_review_result,
                findings=(
                    _finding(title="Leak"),
                    _finding(title="Unguarded divide", severity=Severity.P2, line=8),
                ),
            ),
            prior_state=parse_review_state_v2(body=first),
            head_sha="sha2",
        ),
    )

    assert_that(second).contains(
        "| — | 🔴 P1 | Leak | `src/example.py:10` | round 1 |",
    )
    assert_that(second).contains(
        "| **new** | 🟠 P2 | Unguarded divide | `src/example.py:8` | round 2 |",
    )


def test_resolved_table_stamps_the_fixing_commit(
    sample_review_result: ReviewResult,
) -> None:
    """A resolved finding is struck through and names where it was fixed."""
    first = build_sticky_comment(
        result=_with(base=sample_review_result, findings=(_finding(title="Leak"),)),
        head_sha="0123456789abcdef",
    )

    second = _body_only(
        body=build_sticky_comment(
            result=_with(base=sample_review_result, findings=()),
            prior_state=parse_review_state_v2(body=first),
            head_sha="fedcba9876543210",
        ),
    )

    assert_that(second).contains("### ✔ Resolved (1)")
    assert_that(second).contains("| 🔴 P1 | ~~Leak~~ | `fedcba9` · round 2 |")


# --- summary and reasoning ---------------------------------------------------


def test_summary_bullets_tied_to_blockers_are_severity_marked(
    sample_review_result: ReviewResult,
) -> None:
    """A bullet about an open P1 cannot read as neutral prose."""
    body = _body_only(
        body=build_sticky_comment(
            result=_with(
                base=sample_review_result,
                findings=(_finding(title="Hardcoded password literal"),),
                pr_summary=ReviewSummary(
                    headline="Seeds a sandbox module.",
                    walkthrough=(
                        SummaryBullet(text="Adds three utility functions."),
                        SummaryBullet(
                            text="Stores an application password.",
                            finding_ref="src/example.py:10",
                        ),
                    ),
                ),
            ),
        ),
    )

    assert_that(body).contains("### Summary")
    assert_that(body).contains("- Adds three utility functions.")
    assert_that(body).contains("- 🔴 **Stores an application password.**")


def test_reasoning_section_carries_rubric_and_attention_files(
    sample_review_result: ReviewResult,
) -> None:
    """Model reasoning renders above the derivation fine-print."""
    body = _body_only(
        body=build_sticky_comment(
            result=_with(
                base=sample_review_result,
                findings=(_finding(title="Leak"),),
                verdict_reasoning=VerdictReasoning(
                    deciding_factor="The credential is evaluated at import time.",
                    failure_mechanism="Every importer holds the secret.",
                    files_needing_attention=("src/example.py",),
                ),
            ),
        ),
    )

    assert_that(body).contains("### Why it's blocked")
    assert_that(body).contains("The credential is evaluated at import time.")
    assert_that(body).contains("Every importer holds the secret.")
    assert_that(body).contains("Verdict is derived from open findings")
    assert_that(body).contains("**Files needing attention:** `src/example.py`")


# --- honesty and structure ---------------------------------------------------


def test_this_run_badges_lead_with_the_model_and_name_the_transport(
    sample_review_result: ReviewResult,
) -> None:
    """No figure is presented as billed; the transport badge carries that."""
    body = _body_only(
        body=build_sticky_comment(
            result=sample_review_result,
            transport="cli",
            auth_mode="subscription",
        ),
    )

    assert_that(body).contains("**This run** — model `claude-sonnet-4-20250514`")
    assert_that(body).contains("est. cost")
    assert_that(body).contains("transport `cli · subscription`")


def test_body_never_nests_details_more_than_one_level(
    sample_review_result: ReviewResult,
) -> None:
    """Nested collapsibles are what made the previous sticky unreadable."""
    first = build_sticky_comment(
        result=_with(base=sample_review_result, findings=(_finding(title="Leak"),)),
        head_sha="sha1",
    )
    finding = _finding(title="Unguarded divide", severity=Severity.P2, line=8)

    body = build_sticky_comment(
        result=_with(
            base=sample_review_result,
            findings=(_finding(title="Leak"), finding),
        ),
        prior_state=parse_review_state_v2(body=first),
        head_sha="sha2",
        checklist_display=ChecklistDisplay.ALL,
        inline_failure=InlinePostFailure(
            reason="line not in diff",
            findings=(finding,),
        ),
    )

    assert_that(_max_details_depth(body=body)).is_equal_to(1)


def test_history_collapsible_appears_once_and_only_after_round_one(
    sample_review_result: ReviewResult,
) -> None:
    """History is a single collapsible, absent while there is no history."""
    first = build_sticky_comment(result=sample_review_result, head_sha="sha1")
    assert_that(_body_only(body=first)).does_not_contain("🕘 Run history")

    second = _body_only(
        body=build_sticky_comment(
            result=sample_review_result,
            prior_state=parse_review_state_v2(body=first),
            head_sha="sha2",
        ),
    )

    assert_that(second.count("🕘 Run history")).is_equal_to(1)
    assert_that(second).contains("🕘 Run history — 2 runs")
    assert_that(second).contains("| Run | Commit | Verdict | Model | Open |")
    assert_that(second).contains("**Round 1** · `sha1`")


def test_fix_all_panel_is_scoped_to_all_open_findings(
    sample_review_result: ReviewResult,
) -> None:
    """The panel title and the prompt's first line both restate the scope."""
    first = build_sticky_comment(
        result=_with(base=sample_review_result, findings=(_finding(title="Leak"),)),
        head_sha="sha1",
    )

    second = _body_only(
        body=build_sticky_comment(
            result=_with(base=sample_review_result, findings=(_finding(title="Leak"),)),
            prior_state=parse_review_state_v2(body=first),
            head_sha="sha2",
        ),
    )

    assert_that(second).contains("Fix-all prompt — 1 still-open finding (rounds 1–2)")
    assert_that(second).contains("still open on this PR after round 2")


def test_table_cells_escape_pipes_from_model_titles(
    sample_review_result: ReviewResult,
) -> None:
    """An untrusted title cannot break out of its table cell."""
    body = _body_only(
        body=build_sticky_comment(
            result=_with(
                base=sample_review_result,
                findings=(_finding(title="Leaks a | b secret"),),
            ),
        ),
    )

    assert_that(body).contains("Leaks a \\| b secret")


# --- degraded path -----------------------------------------------------------


def test_degraded_path_warns_and_folds_details_into_the_sticky(
    sample_review_result: ReviewResult,
) -> None:
    """A failed inline post leaves the sticky as the finding's only surface."""
    finding = _finding(title="Hardcoded password literal")

    body = _body_only(
        body=build_sticky_comment(
            result=_with(base=sample_review_result, findings=(finding,)),
            inline_failure=InlinePostFailure(
                reason="review API returned 422 - line not in diff",
                findings=(finding,),
            ),
        ),
    )

    assert_that(body).contains(
        "> ⚠️ **1 finding could not be posted as an inline comment**",
    )
    assert_that(body).contains("review API returned 422 - line not in diff")
    assert_that(body).contains("📋 Details for 1 finding not posted inline")
    # The full detail — normally only on the inline comment — is folded in.
    assert_that(body).contains(
        "Stores an application password as a module-level literal.",
    )
    assert_that(body).contains("**Fix:** Read it from the environment.")


def test_warning_row_precedes_the_open_findings_table(
    sample_review_result: ReviewResult,
) -> None:
    """The reader must see the caveat before the index it applies to."""
    finding = _finding(title="Leak")
    body = _body_only(
        body=build_sticky_comment(
            result=_with(base=sample_review_result, findings=(finding,)),
            inline_failure=InlinePostFailure(reason="422", findings=(finding,)),
        ),
    )

    assert_that(body.index("could not be posted as an inline")).is_less_than(
        body.index("### Open findings"),
    )


def test_no_degraded_content_when_inline_posting_succeeded(
    sample_review_result: ReviewResult,
) -> None:
    """The healthy path never duplicates inline-comment detail."""
    body = _body_only(body=build_sticky_comment(result=sample_review_result))

    assert_that(body).does_not_contain("could not be posted as")
    assert_that(body).does_not_contain("not posted inline")


# --- size capping ------------------------------------------------------------


def test_oldest_history_is_pruned_before_any_finding(
    sample_review_result: ReviewResult,
) -> None:
    """Under size pressure the oldest rounds go first, and findings go last.

    The exact finding count at which the 65,536-char cap starts biting depends
    on every string in the layout, so rather than hard-code one the test sweeps
    a range of finding counts and asserts the invariants at each: the comment
    always fits, the rounds still shown are always the newest ones, any drop is
    announced, and no finding is sacrificed while history remains to shed.
    """
    prior_state = ReviewState(
        runs=tuple(
            RunRecord(
                round=round_number,
                sha=f"{round_number:040d}",
                model="claude-sonnet-4-6",
                p1=1,
                prompt=100,
                completion=200,
                total=300,
                cost=0.01,
                duration=10.0,
            )
            for round_number in range(1, _PRIOR_ROUNDS + 1)
        ),
    )
    saw_partial_history = False

    for count in range(108, 128):
        findings = tuple(
            _finding(
                title=f"Finding {index} " + "x" * 100,
                file=f"src/module_{index}.py",
                line=index,
                severity=Severity.P2,
            )
            for index in range(count)
        )
        body = build_sticky_comment(
            result=_with(base=sample_review_result, findings=findings),
            prior_state=prior_state,
            head_sha="deadbee",
        )
        rendered = _body_only(body=body)
        shown = [int(number) for number in _ROUND_RE.findall(rendered)]

        assert_that(len(body)).is_less_than_or_equal_to(GITHUB_COMMENT_HARD_LIMIT)
        # Whatever survives is the newest contiguous block of rounds.
        assert_that(shown).is_equal_to(
            list(range(_PRIOR_ROUNDS, _PRIOR_ROUNDS - len(shown), -1)),
        )
        if len(shown) < _PRIOR_ROUNDS:
            assert_that(rendered).contains("not listed")
            assert_that(rendered).contains("history truncated")
        if shown:
            # History still had rows to shed, so no finding may be dropped.
            assert_that(rendered).contains(f"### Open findings ({count})")
            assert_that(rendered).does_not_contain("more open findings not listed")
        if 0 < len(shown) < _PRIOR_ROUNDS:
            saw_partial_history = True

    # The oldest-first branch must actually have been exercised, not merely
    # skipped over by an all-or-nothing prune.
    assert_that(saw_partial_history).is_true()


def test_comment_stays_under_the_hard_limit_with_huge_finding_sets(
    sample_review_result: ReviewResult,
) -> None:
    """Open findings are trimmed last, explicitly, and never silently."""
    findings = tuple(
        _finding(
            title=f"Finding number {index} with a reasonably long title",
            file=f"src/module_{index}.py",
            line=index,
            severity=Severity.P2,
        )
        for index in range(400)
    )

    body = build_sticky_comment(
        result=_with(base=sample_review_result, findings=findings),
    )

    assert_that(len(body)).is_less_than_or_equal_to(GITHUB_COMMENT_HARD_LIMIT)
    assert_that(body).contains(STICKY_MARKER)
    rendered = _body_only(body=body)
    assert_that(rendered).contains("### Open findings (400)")
    assert_that(rendered).contains("more open findings not listed")

"""Tests for the redesigned inline finding comment format (#1911)."""

from __future__ import annotations

from dataclasses import replace

import pytest
from assertpy import assert_that

from lintro.ai.review.enums.finding_kind import FindingKind
from lintro.ai.review.enums.fix_mode import FixMode
from lintro.ai.review.enums.suggestion_rejection import SuggestionRejection
from lintro.ai.review.finding_parser import parse_findings
from lintro.ai.review.github_render import format_finding_comment
from lintro.ai.review.inline_fix import (
    MAX_REPLACED_LINES,
    MAX_REPLACEMENT_CHARS,
    InlineFixPlan,
    finding_suggested_change,
    plan_inline_fix,
)
from lintro.ai.review.models.review_finding import ReviewFinding, Severity
from lintro.ai.review.models.suggested_change import (
    SuggestedChange,
    parse_suggested_change,
)

_PATH = "src/example.py"


def _finding(**overrides: object) -> ReviewFinding:
    """Build a P1 finding with a valid single-line suggested change.

    Args:
        **overrides: Field values replacing the defaults.

    Returns:
        The finding.
    """
    base = ReviewFinding(
        severity=Severity.P1,
        category="security",
        file=_PATH,
        line=10,
        title="Hardcoded password literal in source",
        description="The credential is assigned at module scope.",
        cause="It was committed directly instead of read from the environment.",
        fix='read it from `os.environ["APP_PASSWORD"]`',
        confidence="high",
        suggested_change=SuggestedChange(
            start_line=10,
            end_line=10,
            replacement='password = os.environ["APP_PASSWORD"]',
        ),
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


def _round_diff(lines: set[int]) -> dict[str, set[int]]:
    """Build a this-round diff map covering ``lines`` of the sample file.

    Args:
        lines: Line numbers this round's diff changed.

    Returns:
        The diff line map.
    """
    return {_PATH: lines}


# --- mode selection ---------------------------------------------------------


def test_valid_change_on_this_rounds_diff_selects_mode_a() -> None:
    """A hunk on a line this round posted is committable."""
    plan = plan_inline_fix(
        finding=_finding(),
        round_diff_lines=_round_diff({10}),
    )

    assert_that(plan.mode).is_equal_to(FixMode.SUGGESTION)
    assert_that(plan.rejection).is_none()
    assert_that(plan.committable_change).is_not_none()


def test_multiline_change_fully_inside_the_round_diff_selects_mode_a() -> None:
    """Every replaced line, not just the anchor, must be in the round's diff."""
    finding = _finding(
        suggested_change=SuggestedChange(
            start_line=9,
            end_line=11,
            replacement="a\nb\nc",
        ),
    )

    plan = plan_inline_fix(finding=finding, round_diff_lines=_round_diff({9, 10, 11}))

    assert_that(plan.mode).is_equal_to(FixMode.SUGGESTION)


@pytest.mark.parametrize(
    ("finding", "round_diff_lines", "carried_over", "expected"),
    [
        pytest.param(
            _finding(suggested_change=None),
            _round_diff({10}),
            False,
            SuggestionRejection.NO_SUGGESTED_CHANGE,
            id="no-change",
        ),
        pytest.param(
            _finding(
                suggested_change=SuggestedChange(
                    start_line=10,
                    end_line=10,
                    replacement="   \n  ",
                ),
            ),
            _round_diff({10}),
            False,
            SuggestionRejection.EMPTY_REPLACEMENT,
            id="blank-replacement",
        ),
        pytest.param(
            _finding(
                suggested_change=SuggestedChange(
                    start_line=10,
                    end_line=10,
                    replacement="x" * (MAX_REPLACEMENT_CHARS + 1),
                ),
            ),
            _round_diff({10}),
            False,
            SuggestionRejection.REPLACEMENT_TOO_LARGE,
            id="oversized-replacement",
        ),
        pytest.param(
            _finding(
                suggested_change=SuggestedChange(
                    start_line=10,
                    end_line=10 + MAX_REPLACED_LINES,
                    replacement="x",
                ),
            ),
            _round_diff({10}),
            False,
            SuggestionRejection.SPAN_TOO_LARGE,
            id="oversized-span",
        ),
        pytest.param(
            _finding(
                suggested_change=SuggestedChange(
                    start_line=12,
                    end_line=10,
                    replacement="x",
                ),
            ),
            _round_diff({10, 11, 12}),
            False,
            SuggestionRejection.INVALID_RANGE,
            id="reversed-range",
        ),
        pytest.param(
            _finding(
                suggested_change=SuggestedChange(
                    start_line=0,
                    end_line=10,
                    replacement="x",
                ),
            ),
            _round_diff({10}),
            False,
            SuggestionRejection.INVALID_RANGE,
            id="non-positive-start",
        ),
        pytest.param(
            _finding(
                suggested_change=SuggestedChange(
                    start_line=20,
                    end_line=21,
                    replacement="x",
                ),
            ),
            _round_diff({20, 21}),
            False,
            SuggestionRejection.ANCHOR_OUTSIDE_RANGE,
            id="anchor-outside-range",
        ),
        pytest.param(
            _finding(),
            _round_diff({10}),
            True,
            SuggestionRejection.CARRIED_OVER,
            id="carried-over",
        ),
        pytest.param(
            _finding(),
            None,
            False,
            SuggestionRejection.NO_ROUND_DIFF,
            id="round-diff-unknown",
        ),
        pytest.param(
            _finding(),
            _round_diff({11}),
            False,
            SuggestionRejection.LINES_NOT_IN_ROUND_DIFF,
            id="line-not-posted-this-round",
        ),
        pytest.param(
            _finding(
                suggested_change=SuggestedChange(
                    start_line=9,
                    end_line=10,
                    replacement="a\nb",
                ),
            ),
            _round_diff({10}),
            False,
            SuggestionRejection.LINES_NOT_IN_ROUND_DIFF,
            id="range-partly-outside-round-diff",
        ),
    ],
)
def test_every_invalid_case_falls_back_to_mode_b(
    finding: ReviewFinding,
    round_diff_lines: dict[str, set[int]] | None,
    carried_over: bool,
    expected: SuggestionRejection,
) -> None:
    """Each validity failure falls back to mode B and names its reason."""
    plan = plan_inline_fix(
        finding=finding,
        round_diff_lines=round_diff_lines,
        carried_over=carried_over,
    )

    assert_that(plan.mode).is_equal_to(FixMode.DESCRIBED)
    assert_that(plan.rejection).is_equal_to(expected)
    assert_that(plan.committable_change).is_none()


@pytest.mark.parametrize(
    "path",
    [
        pytest.param("./src\\example.py", id="dot-slash-backslash"),
        pytest.param(".\\src\\example.py", id="windows-relative"),
        pytest.param("  src/example.py  ", id="padded"),
    ],
)
def test_awkward_paths_still_match_the_round_diff(path: str) -> None:
    """Odd path spellings still resolve against the API's forward-slash keys."""
    finding = _finding(file=path)

    plan = plan_inline_fix(finding=finding, round_diff_lines=_round_diff({10}))

    assert_that(plan.mode).is_equal_to(FixMode.SUGGESTION)


def test_legacy_suggested_code_is_read_as_a_single_line_change() -> None:
    """A finding predating suggested_change still produces a committable hunk."""
    finding = _finding(suggested_change=None, suggested_code="    return EXPIRED")

    change = finding_suggested_change(finding=finding)

    assert_that(change).is_not_none()
    assert_that(change.start_line).is_equal_to(10)  # type: ignore[union-attr]
    assert_that(change.end_line).is_equal_to(10)  # type: ignore[union-attr]


# --- rendering --------------------------------------------------------------


def test_mode_a_renders_both_the_suggestion_and_the_prompt() -> None:
    """Mode A serves click-to-commit and agent users at once."""
    finding = _finding()
    body = format_finding_comment(
        finding=finding,
        inline_fix=plan_inline_fix(
            finding=finding,
            round_diff_lines=_round_diff({10}),
        ),
    )

    assert_that(body).contains("```suggestion")
    assert_that(body).contains('password = os.environ["APP_PASSWORD"]')
    assert_that(body).contains("⚡ **Prompt for AI agents**")
    # The prompt must land the same edit the suggestion would.
    assert_that(body).contains("Apply exactly the change already proposed")
    assert_that(body).contains("replace line 10 with the following, verbatim")
    # Mode A's fix slot is the suggestion, not a described one-liner.
    assert_that(body).does_not_contain("**Fix:**")


def test_mode_b_renders_a_highlighted_fix_line_and_no_suggestion() -> None:
    """Mode B keeps the prompt panel but has nothing to commit."""
    finding = _finding()
    body = format_finding_comment(
        finding=finding,
        inline_fix=plan_inline_fix(finding=finding, round_diff_lines=None),
    )

    assert_that(body).does_not_contain("```suggestion")
    assert_that(body).contains('**Fix:** read it from `os.environ["APP_PASSWORD"]`')
    assert_that(body).contains("⚡ **Prompt for AI agents**")
    assert_that(body).does_not_contain("Apply exactly the change already proposed")


def test_multiline_mode_a_prompt_names_the_full_range() -> None:
    """A multi-line suggestion tells the agent the whole span it replaces."""
    finding = _finding(
        suggested_change=SuggestedChange(
            start_line=9,
            end_line=11,
            replacement="first\nsecond\nthird",
        ),
    )
    body = format_finding_comment(
        finding=finding,
        inline_fix=plan_inline_fix(
            finding=finding,
            round_diff_lines=_round_diff({9, 10, 11}),
        ),
    )

    assert_that(body).contains("replace lines 9-11 with the following, verbatim")
    assert_that(body).contains("first")
    assert_that(body).contains("third")


def test_mode_a_prompt_carries_the_replacement_untruncated() -> None:
    """The prompt and the suggestion must be byte-identical, however long."""
    long_line = "x" * (MAX_REPLACEMENT_CHARS - 100)
    finding = _finding(
        suggested_change=SuggestedChange(
            start_line=10,
            end_line=10,
            replacement=long_line,
        ),
    )
    body = format_finding_comment(
        finding=finding,
        inline_fix=plan_inline_fix(
            finding=finding,
            round_diff_lines=_round_diff({10}),
        ),
    )

    # Once inside the suggestion block, once inside the prompt panel.
    assert_that(body.count(long_line)).is_equal_to(2)
    assert_that(body).does_not_contain("…")


def test_reasoning_is_fully_visible_with_no_collapsible() -> None:
    """Cause and description render in the body, not behind a details tag."""
    finding = _finding()
    body = format_finding_comment(
        finding=finding,
        inline_fix=plan_inline_fix(finding=finding, round_diff_lines=None),
    )

    reasoning_end = body.index("**Fix:**")
    assert_that(body[:reasoning_end]).contains(
        "The credential is assigned at module scope.",
    )
    assert_that(body[:reasoning_end]).contains(
        "**Root cause:** It was committed directly",
    )
    assert_that(body).does_not_contain("Why this matters")


def test_header_carries_severity_category_and_confidence() -> None:
    """The header is a chip row, not a repeated severity heading."""
    finding = _finding()
    body = format_finding_comment(
        finding=finding,
        inline_fix=plan_inline_fix(finding=finding, round_diff_lines=None),
    )

    assert_that(body.splitlines()[0]).is_equal_to(
        "🔴 **P1** · `security` · `high confidence`",
    )
    assert_that(body).contains("**Hardcoded password literal in source**")


def test_comment_has_no_footer() -> None:
    """The redundant ``lintro · category`` footer is gone."""
    # A P3 has no prompt panel, so the fix line is the last thing in the body —
    # nothing may follow it.
    finding = _finding(severity=Severity.P3)
    body = format_finding_comment(
        finding=finding,
        inline_fix=plan_inline_fix(finding=finding, round_diff_lines=None),
    )

    assert_that(body).does_not_contain("<sub>lintro ·")
    assert_that(body.rstrip().splitlines()[-1]).starts_with("**Fix:**")


@pytest.mark.parametrize(
    ("severity", "expected"),
    [
        pytest.param(Severity.P1, True, id="p1"),
        pytest.param(Severity.P2, True, id="p2"),
        pytest.param(Severity.P3, False, id="p3"),
    ],
)
def test_prompt_panel_is_gated_to_p1_and_p2(
    severity: Severity,
    expected: bool,
) -> None:
    """A nit gets no prompt panel; blockers and warnings do."""
    finding = _finding(severity=severity)
    body = format_finding_comment(
        finding=finding,
        inline_fix=plan_inline_fix(finding=finding, round_diff_lines=None),
    )

    assert_that("⚡ **Prompt for AI agents**" in body).is_equal_to(expected)


def test_p3_mode_a_keeps_the_suggestion_without_a_prompt() -> None:
    """Prompt gating does not cost a P3 its one-click fix."""
    finding = _finding(severity=Severity.P3)
    body = format_finding_comment(
        finding=finding,
        inline_fix=plan_inline_fix(
            finding=finding,
            round_diff_lines=_round_diff({10}),
        ),
    )

    assert_that(body).contains("```suggestion")
    assert_that(body).does_not_contain("⚡ **Prompt for AI agents**")


def test_question_entries_get_no_prompt_panel() -> None:
    """A question has nothing to fix, so it has nothing to prompt for."""
    finding = _finding(kind=FindingKind.QUESTION)
    body = format_finding_comment(
        finding=finding,
        inline_fix=plan_inline_fix(finding=finding, round_diff_lines=None),
    )

    assert_that(body).does_not_contain("⚡ **Prompt for AI agents**")


def test_embedded_rendering_omits_suggestion_and_prompt() -> None:
    """Without an inline plan, neither affordance would work — so neither renders."""
    body = format_finding_comment(finding=_finding())

    assert_that(body).does_not_contain("```suggestion")
    assert_that(body).does_not_contain("⚡ **Prompt for AI agents**")
    assert_that(body).contains("**Fix:**")


def test_mentions_in_the_replacement_cannot_ping_users() -> None:
    """Untrusted replacement code is sanitized before it reaches the comment."""
    finding = _finding(
        suggested_change=SuggestedChange(
            start_line=10,
            end_line=10,
            replacement="# owner @team\npassword = ENV",
        ),
    )
    body = format_finding_comment(
        finding=finding,
        inline_fix=plan_inline_fix(
            finding=finding,
            round_diff_lines=_round_diff({10}),
        ),
    )

    assert_that(body).contains("```suggestion")
    assert_that(body).does_not_contain("@team")


def test_plan_default_is_mode_b() -> None:
    """A plan constructed without a mode-A change never renders a suggestion."""
    assert_that(
        InlineFixPlan(mode=FixMode.DESCRIBED).committable_change,
    ).is_none()


# --- schema round-trip ------------------------------------------------------


def test_suggested_change_round_trips_through_the_finding_schema() -> None:
    """A suggested_change survives model payload to model and back."""
    payload = {
        "severity": "P2",
        "category": "logic-bug",
        "file": _PATH,
        "line": 8,
        "title": "divide() raises unguarded ZeroDivisionError",
        "description": "d",
        "cause": "c",
        "fix": "guard the divisor",
        "confidence": "high",
        "suggested_change": {
            "lines": [7, 8],
            "replacement": "def divide(a, b):\n    return a / b if b else 0",
        },
    }

    findings = parse_findings(raw_findings=[payload])

    assert_that(findings).is_length(1)
    change = findings[0].suggested_change
    assert_that(change).is_equal_to(
        SuggestedChange(
            start_line=7,
            end_line=8,
            replacement="def divide(a, b):\n    return a / b if b else 0",
        ),
    )
    assert_that(change.to_dict()).is_equal_to(  # type: ignore[union-attr]
        payload["suggested_change"],
    )


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(None, id="absent"),
        pytest.param("lines 1-2", id="not-a-mapping"),
        pytest.param({"lines": [1, 2]}, id="no-replacement"),
        pytest.param({"replacement": "x"}, id="no-lines"),
        pytest.param({"lines": [1], "replacement": "x"}, id="short-range"),
        pytest.param({"lines": [1, 2, 3], "replacement": "x"}, id="long-range"),
        pytest.param({"lines": [1, 2], "replacement": 7}, id="non-string-replacement"),
    ],
)
def test_malformed_suggested_change_degrades_to_none(raw: object) -> None:
    """A malformed payload costs the suggestion, never the review."""
    assert_that(parse_suggested_change(raw)).is_none()


def test_non_numeric_lines_coerce_rather_than_crash() -> None:
    """String line numbers from a sloppy model still parse."""
    change = parse_suggested_change({"lines": ["7", "8"], "replacement": "x"})

    assert_that(change).is_equal_to(
        SuggestedChange(start_line=7, end_line=8, replacement="x"),
    )

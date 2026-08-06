"""Tests for the shared run-stats badge tables (issue #1955).

The sticky comment's ``This run`` section and the per-review body's run stats
render the same data, so they render it through one helper. These tests pin the
helper's own contract and the fact that both surfaces still agree.
"""

from __future__ import annotations

from dataclasses import replace

from assertpy import assert_that

from lintro.ai.review.finding_matcher import match_findings
from lintro.ai.review.github_render import (
    format_badge_table,
    format_badge_tables,
    run_stats_primary_cells,
)
from lintro.ai.review.github_review_body import build_review_body
from lintro.ai.review.github_sticky import build_sticky_comment
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.review_state import ReviewState

_PRIMARY_HEADER = "| model | est. cost | tokens in | tokens out |"


def _sticky(*, result: ReviewResult) -> str:
    """Render the sticky comment for ``result`` with a known transport."""
    return build_sticky_comment(
        result=result,
        transport="cli",
        auth_mode="subscription",
    )


def _body(*, result: ReviewResult) -> str:
    """Render the per-review body for ``result`` with a known transport."""
    prior_state = ReviewState()
    match = match_findings(
        previous=prior_state,
        findings=result.findings,
        round_number=prior_state.next_round,
        head_sha="fb740b2",
    )
    return build_review_body(
        result=result,
        prior_state=prior_state,
        match=match,
        head_sha="fb740b2",
        transport="cli",
        auth_mode="subscription",
    )


def _value_row(*, text: str, header: str) -> str:
    """Return the value row that follows ``header`` in ``text``.

    The header is asserted rather than indexed blindly: a header-format
    regression should fail as a named assertion, not as a bare ``ValueError``
    from :meth:`list.index` that says nothing about what was being looked for.
    """
    lines = text.splitlines()
    assert_that(lines).contains(header)
    return lines[lines.index(header) + 2]


# --- helper contract ---------------------------------------------------------


def test_badge_table_renders_one_row_of_label_value_pairs() -> None:
    """A badge table is a header row, a divider, and exactly one value row."""
    lines = format_badge_table(cells=[("model", "`gpt`"), ("depth", "2")])

    assert_that(lines).is_equal_to(
        ["| model | depth |", "| --- | --- |", "| `gpt` | 2 |"],
    )


def test_badge_table_escapes_pipes_so_a_row_cannot_shear() -> None:
    """An unescaped pipe in a value would end its cell and break the table."""
    lines = format_badge_table(cells=[("model", "`a|b`")])

    assert_that(lines[-1]).is_equal_to("| `a\\|b` |")


def test_badge_table_escapes_a_backslash_before_the_pipe_it_precedes() -> None:
    """Escaping only the pipe would leave it delimiter-readable again."""
    lines = format_badge_table(cells=[("model", "a\\|b")])

    assert_that(lines[-1]).is_equal_to("| a\\\\\\|b |")


def test_badge_table_renders_nothing_for_no_cells() -> None:
    """An empty row must not emit a headerless table skeleton."""
    assert_that(format_badge_table(cells=[])).is_empty()


def test_badge_tables_separate_rows_with_a_blank_line() -> None:
    """Consecutive tables need a blank line or GFM merges them into one."""
    lines = format_badge_tables(rows=[[("a", "1")], [("b", "2")]])

    assert_that(lines).is_equal_to(
        [
            "| a |",
            "| --- |",
            "| 1 |",
            "",
            "| b |",
            "| --- |",
            "| 2 |",
        ],
    )


def test_badge_tables_skip_empty_rows() -> None:
    """An empty group is dropped rather than emitting a stray separator."""
    lines = format_badge_tables(rows=[[], [("b", "2")]])

    assert_that(lines).is_equal_to(["| b |", "| --- |", "| 2 |"])


def test_primary_cells_are_ordered_model_cost_tokens(
    sample_review_result: ReviewResult,
) -> None:
    """The shared ordering rule of epic #1905 is model-first on every surface."""
    cells = run_stats_primary_cells(metadata=sample_review_result.metadata)

    assert_that([label for label, _ in cells]).is_equal_to(
        ["model", "est. cost", "tokens in", "tokens out"],
    )


def test_primary_cells_prefix_estimated_values_with_a_tilde(
    sample_review_result: ReviewResult,
) -> None:
    """Estimated figures are never presented as billed ones."""
    result = replace(
        sample_review_result,
        metadata=replace(
            sample_review_result.metadata,
            token_usage_estimated=True,
        ),
    )

    values = dict(run_stats_primary_cells(metadata=result.metadata))

    assert_that(values["est. cost"]).starts_with("~$")
    assert_that(values["tokens in"]).is_equal_to("~1,000")
    assert_that(values["tokens out"]).is_equal_to("~200")


def test_primary_cells_omit_the_tilde_for_reported_values(
    sample_review_result: ReviewResult,
) -> None:
    """Provider-reported figures carry no estimation marker."""
    values = dict(run_stats_primary_cells(metadata=sample_review_result.metadata))

    assert_that(values["est. cost"]).is_equal_to("$0.0500")
    assert_that(values["tokens in"]).is_equal_to("1,000")


# --- both surfaces agree -----------------------------------------------------


def test_both_surfaces_render_the_same_primary_badge_table(
    sample_review_result: ReviewResult,
) -> None:
    """One renderer means the sticky and the body cannot drift apart."""
    sticky = _sticky(result=sample_review_result)
    body = _body(result=sample_review_result)

    assert_that(sticky).contains(_PRIMARY_HEADER)
    assert_that(body).contains(_PRIMARY_HEADER)
    assert_that(_value_row(text=sticky, header=_PRIMARY_HEADER)).is_equal_to(
        _value_row(text=body, header=_PRIMARY_HEADER),
    )


def test_sticky_this_run_section_renders_two_badge_tables(
    sample_review_result: ReviewResult,
) -> None:
    """Snapshot of the sticky's This-run block, heading included."""
    sticky = _sticky(result=sample_review_result)
    start = sticky.index("**This run**")
    section = sticky[start:].split("\n\n<sub>", maxsplit=1)[0].rstrip()

    assert_that(section).is_equal_to(
        "\n".join(
            [
                "**This run**",
                "",
                _PRIMARY_HEADER,
                "| --- | --- | --- | --- |",
                "| `claude-sonnet-4-20250514` | $0.0500 | 1,000 | 200 |",
                "",
                "| transport | depth | files | checks | duration |",
                "| --- | --- | --- | --- | --- |",
                "| cli · subscription | 2 | 3 | 3 | 0s |",
            ],
        ),
    )


def test_sticky_this_run_tables_keep_the_estimated_prefix(
    sample_review_result: ReviewResult,
) -> None:
    """The ``~`` prefix survived the move from prose lines to tables."""
    result = replace(
        sample_review_result,
        metadata=replace(
            sample_review_result.metadata,
            token_usage_estimated=True,
        ),
    )

    sticky = _sticky(result=result)

    assert_that(_value_row(text=sticky, header=_PRIMARY_HEADER)).is_equal_to(
        "| `claude-sonnet-4-20250514` | ~$0.0500 | ~1,000 | ~200 |",
    )

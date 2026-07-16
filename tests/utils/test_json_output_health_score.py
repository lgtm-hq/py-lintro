"""Tests that the health score is added additively to JSON output."""

from assertpy import assert_that

from lintro.models.core.tool_result import ToolResult
from lintro.utils.health_score import SeverityCounts, compute_health_score
from lintro.utils.json_output import create_json_output


def _result() -> ToolResult:
    """Build a simple successful tool result.

    Returns:
        ToolResult: A minimal check-mode result.
    """
    return ToolResult(name="ruff", success=True, issues_count=0)


def test_json_summary_omits_health_score_when_not_provided() -> None:
    """The existing summary schema is unchanged when no score is passed."""
    data = create_json_output(
        action="check",
        results=[_result()],
        total_issues=0,
        total_fixed=0,
        total_remaining=0,
        exit_code=0,
    )

    assert_that(data).contains_key("results", "summary")
    assert_that(data["summary"]).contains_key(
        "total_issues",
        "total_fixed",
        "total_remaining",
    )
    assert_that(data["summary"]).does_not_contain_key("health_score")


def test_json_summary_includes_health_score_additively() -> None:
    """When provided, the score nests under summary without disturbing keys."""
    health = compute_health_score(SeverityCounts(errors=1, warnings=2))

    data = create_json_output(
        action="check",
        results=[_result()],
        total_issues=3,
        total_fixed=0,
        total_remaining=3,
        exit_code=1,
        health_score=health.to_dict(),
    )

    # Existing keys remain intact.
    assert_that(data["summary"]).contains_key(
        "total_issues",
        "total_fixed",
        "total_remaining",
    )
    assert_that(data["summary"]["total_issues"]).is_equal_to(3)

    # Additive score block.
    score_block = data["summary"]["health_score"]
    assert_that(score_block["score"]).is_equal_to(health.score)
    assert_that(score_block["tier"]).is_equal_to(health.tier.label)
    assert_that(score_block["severity_counts"]).is_equal_to(
        {"error": 1, "warning": 2, "info": 0},
    )

"""Tests for the deterministic 0-100 health score."""

from dataclasses import dataclass

import pytest
from assertpy import assert_that

from lintro.config.score_config import ScoreConfig
from lintro.enums.severity_level import SeverityLevel
from lintro.utils.health_score import (
    MAX_SCORE,
    MIN_SCORE,
    HealthScore,
    ScoreTier,
    SeverityCounts,
    build_shields_badge_markdown,
    build_shields_badge_url,
    compute_health_score,
    compute_health_score_from_config,
    count_severities,
    health_score_for_results,
    shields_color_for_tier,
    tier_for_score,
)


@dataclass
class _FakeIssue:
    """Minimal issue exposing a fixed severity via get_severity()."""

    level: SeverityLevel

    def get_severity(self) -> SeverityLevel:
        """Return the fixed severity for this fake issue.

        Returns:
            SeverityLevel: The configured severity.
        """
        return self.level


@dataclass
class _FakeResult:
    """Minimal tool result carrying a list of issues."""

    issues: list[_FakeIssue] | None


def _errors(n: int) -> SeverityCounts:
    """Build counts with ``n`` error issues.

    Args:
        n: Number of error issues.

    Returns:
        SeverityCounts: Counts with only errors populated.
    """
    return SeverityCounts(errors=n)


def test_zero_issues_scores_exactly_100() -> None:
    """A clean run must score exactly 100."""
    result = compute_health_score(SeverityCounts())

    assert_that(result.score).is_equal_to(100)
    assert_that(result.tier).is_equal_to(ScoreTier.GREAT)
    assert_that(result.weighted_penalty).is_equal_to(0.0)


def test_any_issue_drops_below_100() -> None:
    """A single issue of any severity must push the score below 100."""
    assert_that(compute_health_score(SeverityCounts(info=1)).score).is_less_than(100)
    assert_that(compute_health_score(SeverityCounts(warnings=1)).score).is_less_than(
        100,
    )
    assert_that(compute_health_score(SeverityCounts(errors=1)).score).is_less_than(100)


def test_score_is_bounded() -> None:
    """Scores stay within [0, 100] even for extreme inputs."""
    huge = compute_health_score(SeverityCounts(errors=10_000_000))

    assert_that(huge.score).is_greater_than_or_equal_to(MIN_SCORE)
    assert_that(huge.score).is_less_than_or_equal_to(MAX_SCORE)


def test_error_weighs_more_than_warning_more_than_info() -> None:
    """One error must hurt more than one warning, which hurts more than info."""
    err = compute_health_score(SeverityCounts(errors=1)).score
    warn = compute_health_score(SeverityCounts(warnings=1)).score
    info = compute_health_score(SeverityCounts(info=1)).score

    assert_that(err).is_less_than(warn)
    assert_that(warn).is_less_than(info)


def test_monotonic_in_error_count() -> None:
    """Adding errors never increases the score."""
    previous = 101
    for n in range(0, 30):
        current = compute_health_score(_errors(n)).score
        assert_that(current).is_less_than_or_equal_to(previous)
        previous = current


def test_ten_errors_hits_fifty_with_defaults() -> None:
    """Default weights/scale put ten errors exactly on the 50 boundary."""
    result = compute_health_score(SeverityCounts(errors=10))

    assert_that(result.weighted_penalty).is_equal_to(100.0)
    assert_that(result.score).is_equal_to(50)
    assert_that(result.tier).is_equal_to(ScoreTier.NEEDS_WORK)


def test_known_formula_values() -> None:
    """Spot-check the exact floor() outputs of the formula."""
    # weighted = 10 -> 100*100/110 = 90.9 -> floor 90
    assert_that(compute_health_score(SeverityCounts(errors=1)).score).is_equal_to(90)
    # weighted = 3 -> 100*100/103 = 97.08 -> floor 97
    assert_that(compute_health_score(SeverityCounts(warnings=1)).score).is_equal_to(97)
    # weighted = 1 -> 100*100/101 = 99.0 -> floor 99
    assert_that(compute_health_score(SeverityCounts(info=1)).score).is_equal_to(99)


def test_tier_boundaries() -> None:
    """Tier mapping respects the documented inclusive lower bounds."""
    assert_that(tier_for_score(100)).is_equal_to(ScoreTier.GREAT)
    assert_that(tier_for_score(75)).is_equal_to(ScoreTier.GREAT)
    assert_that(tier_for_score(74)).is_equal_to(ScoreTier.NEEDS_WORK)
    assert_that(tier_for_score(50)).is_equal_to(ScoreTier.NEEDS_WORK)
    assert_that(tier_for_score(49)).is_equal_to(ScoreTier.CRITICAL)
    assert_that(tier_for_score(0)).is_equal_to(ScoreTier.CRITICAL)


def test_tier_labels() -> None:
    """Tier labels are the hyphenated, lower-case display forms."""
    assert_that(ScoreTier.GREAT.label).is_equal_to("great")
    assert_that(ScoreTier.NEEDS_WORK.label).is_equal_to("needs-work")
    assert_that(ScoreTier.CRITICAL.label).is_equal_to("critical")


def test_invalid_scale_raises() -> None:
    """A non-positive scale is rejected."""
    assert_that(compute_health_score).raises(ValueError).when_called_with(
        SeverityCounts(errors=1),
        scale=0,
    )


def test_severity_counts_total() -> None:
    """SeverityCounts.total sums all severities."""
    counts = SeverityCounts(errors=2, warnings=3, info=5)

    assert_that(counts.total).is_equal_to(10)


def test_count_severities_from_results() -> None:
    """count_severities tallies severities across results and issues."""
    results = [
        _FakeResult(
            issues=[
                _FakeIssue(SeverityLevel.ERROR),
                _FakeIssue(SeverityLevel.WARNING),
            ],
        ),
        _FakeResult(issues=None),
        _FakeResult(issues=[_FakeIssue(SeverityLevel.INFO)]),
    ]

    counts = count_severities(results)

    assert_that(counts.errors).is_equal_to(1)
    assert_that(counts.warnings).is_equal_to(1)
    assert_that(counts.info).is_equal_to(1)


def test_count_severities_ignores_issues_without_severity() -> None:
    """Issues lacking get_severity() are skipped gracefully."""

    @dataclass
    class _Bare:
        message: str = ""

    results = [_FakeResult(issues=None), type("R", (), {"issues": [_Bare()]})()]

    counts = count_severities(results)

    assert_that(counts.total).is_equal_to(0)


def test_config_weights_change_score() -> None:
    """Custom weights alter the computed score."""
    counts = SeverityCounts(warnings=5)
    default = compute_health_score_from_config(counts, None)
    harsh = compute_health_score_from_config(
        counts,
        ScoreConfig(warning_weight=50.0),
    )

    assert_that(harsh.score).is_less_than(default.score)


def test_config_none_matches_defaults() -> None:
    """Passing None uses the built-in default weights and scale."""
    counts = SeverityCounts(errors=3, warnings=2)

    assert_that(
        compute_health_score_from_config(counts, None).score,
    ).is_equal_to(compute_health_score(counts).score)


def test_health_score_for_results_end_to_end() -> None:
    """health_score_for_results tallies and scores in one call."""
    results = [
        _FakeResult(issues=[_FakeIssue(SeverityLevel.ERROR)]),
        _FakeResult(issues=[_FakeIssue(SeverityLevel.WARNING)]),
    ]

    health = health_score_for_results(results)

    assert_that(health).is_instance_of(HealthScore)
    # weighted = 10 + 3 = 13 -> 100*100/113 = 88.49 -> floor 88
    assert_that(health.score).is_equal_to(88)


def test_to_dict_shape() -> None:
    """The serialized score has a stable, JSON-safe shape."""
    health = compute_health_score(SeverityCounts(errors=2, warnings=3, info=5))

    data = health.to_dict()

    assert_that(data).contains_key("score", "tier", "severity_counts")
    assert_that(data).contains_key("weighted_penalty")
    assert_that(data["severity_counts"]).is_equal_to(
        {"error": 2, "warning": 3, "info": 5},
    )
    assert_that(data["tier"]).is_equal_to(health.tier.label)


@pytest.mark.parametrize(
    ("counts", "expected_tier"),
    [
        (SeverityCounts(), ScoreTier.GREAT),
        (SeverityCounts(warnings=10), ScoreTier.GREAT),
        (SeverityCounts(errors=5), ScoreTier.NEEDS_WORK),
        (SeverityCounts(errors=20), ScoreTier.CRITICAL),
    ],
)
def test_tier_for_representative_counts(
    counts: SeverityCounts,
    expected_tier: ScoreTier,
) -> None:
    """Representative issue mixes land in the expected tiers.

    Args:
        counts: Severity counts under test.
        expected_tier: Tier the score should fall into.
    """
    assert_that(compute_health_score(counts).tier).is_equal_to(expected_tier)

@pytest.mark.parametrize(
    ("tier", "expected_color"),
    [
        (ScoreTier.GREAT, "brightgreen"),
        (ScoreTier.NEEDS_WORK, "yellow"),
        (ScoreTier.CRITICAL, "red"),
    ],
)
def test_shields_color_for_tier(
    tier: ScoreTier,
    expected_color: str,
) -> None:
    """Each score tier maps to the documented shields.io color.

    Args:
        tier: Qualitative score tier.
        expected_color: Expected shields.io color token.
    """
    assert_that(shields_color_for_tier(tier)).is_equal_to(expected_color)


def test_build_shields_badge_url_and_markdown() -> None:
    """Badge helpers encode the score and optional style correctly."""
    url = build_shields_badge_url(84)
    markdown = build_shields_badge_markdown(84, style="flat")

    assert_that(url).is_equal_to(
        "https://img.shields.io/badge/lintro-84%2F100-brightgreen",
    )
    assert_that(markdown).is_equal_to(
        "![Lintro Score](https://img.shields.io/badge/lintro-84%2F100-brightgreen"
        "?style=flat)",
    )

"""Tests for the ``lintro badge`` CLI command."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli import cli
from lintro.cli_utils.commands.badge import (
    badge_command,
    resolve_health_score,
)
from lintro.models.core.run_artifact import RunArtifact
from lintro.utils.health_score import HealthScore, ScoreTier, SeverityCounts


def test_badge_markdown_default() -> None:
    """Default output is a markdown shields.io image for the score."""
    runner = CliRunner()

    result = runner.invoke(badge_command, ["--score", "84"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output.strip()).is_equal_to(
        "![Lintro Score](https://img.shields.io/badge/lintro-84%2F100-brightgreen)",
    )


def test_badge_url_only() -> None:
    """``--url`` prints the bare shields.io URL."""
    runner = CliRunner()

    result = runner.invoke(badge_command, ["--score", "60", "--url"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output.strip()).is_equal_to(
        "https://img.shields.io/badge/lintro-60%2F100-yellow",
    )


def test_badge_style_flat() -> None:
    """``--style flat`` appends the shields style query parameter."""
    runner = CliRunner()

    result = runner.invoke(badge_command, ["--score", "84", "--style", "flat", "--url"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output.strip()).is_equal_to(
        "https://img.shields.io/badge/lintro-84%2F100-brightgreen?style=flat",
    )


def test_badge_json_output() -> None:
    """``--json`` emits score, tier, color, url, and markdown."""
    runner = CliRunner()

    result = runner.invoke(badge_command, ["--score", "40", "--json"])

    assert_that(result.exit_code).is_equal_to(0)
    payload = json.loads(result.output)
    assert_that(payload["score"]).is_equal_to(40)
    assert_that(payload["tier"]).is_equal_to("critical")
    assert_that(payload["color"]).is_equal_to("red")
    assert_that(payload["url"]).is_equal_to(
        "https://img.shields.io/badge/lintro-40%2F100-red",
    )
    assert_that(payload["markdown"]).starts_with("![Lintro Score](")


def test_badge_registered_on_cli() -> None:
    """The root CLI exposes the badge command help."""
    runner = CliRunner()

    result = runner.invoke(cli, ["badge", "--help"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output.lower()).contains("shields")


def test_resolve_health_score_uses_override() -> None:
    """An explicit override skips the live check API."""
    with patch("lintro.cli_utils.commands.badge.api.check_run") as mock_check:
        score = resolve_health_score(score_override=77, paths=())

    assert_that(score).is_equal_to(77)
    mock_check.assert_not_called()


def test_resolve_health_score_runs_check_when_needed() -> None:
    """Without an override, a score-only API check is invoked."""
    artifact = RunArtifact(
        health=HealthScore(
            score=88,
            tier=ScoreTier.GREAT,
            counts=SeverityCounts(),
            weighted_penalty=0.0,
        ),
    )

    with patch(
        "lintro.cli_utils.commands.badge.api.check_run",
        return_value=artifact,
    ) as mock_check:
        score = resolve_health_score(score_override=None, paths=(".",))

    assert_that(score).is_equal_to(88)
    mock_check.assert_called_once()
    assert_that(mock_check.call_args.kwargs["score"]).is_true()
    assert_that(mock_check.call_args.kwargs["no_log"]).is_true()


@pytest.mark.parametrize(
    ("score", "color_fragment"),
    [
        ("100", "brightgreen"),
        ("75", "brightgreen"),
        ("74", "yellow"),
        ("50", "yellow"),
        ("49", "red"),
        ("0", "red"),
    ],
)
def test_badge_color_thresholds(score: str, color_fragment: str) -> None:
    """Badge color tracks the documented tier thresholds.

    Args:
        score: Override score passed to the CLI.
        color_fragment: Expected shields.io color token in the URL.
    """
    runner = CliRunner()

    result = runner.invoke(badge_command, ["--score", score, "--url"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).contains(color_fragment)

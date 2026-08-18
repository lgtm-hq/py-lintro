"""Tests for the ``lintro badge`` CLI command."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import click
import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli import cli
from lintro.cli_utils.commands.badge import (
    badge_command,
    resolve_health_score,
)
from lintro.models.core.run_artifact import RunArtifact
from lintro.models.core.tool_result import ToolResult
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


def _scored_artifact(*, score: int = 88) -> RunArtifact:
    """Build a scored run artifact for badge tests.

    Args:
        score: Health score to embed on the artifact.

    Returns:
        RunArtifact: Artifact with a real health score.
    """
    return RunArtifact(
        health=HealthScore(
            score=score,
            tier=ScoreTier.GREAT,
            counts=SeverityCounts(),
            weighted_penalty=0.0,
        ),
        tool_results=[
            ToolResult(name="ruff", success=True, skipped=False),
        ],
    )


def test_resolve_health_score_runs_check_when_needed() -> None:
    """Without an override, a score-only API check is invoked."""
    artifact = _scored_artifact()

    with patch(
        "lintro.cli_utils.commands.badge.api.check_run",
        return_value=artifact,
    ) as mock_check:
        score = resolve_health_score(score_override=None, paths=(".",))

    assert_that(score).is_equal_to(88)
    mock_check.assert_called_once()
    assert_that(mock_check.call_args.kwargs["score"]).is_true()
    assert_that(mock_check.call_args.kwargs["no_log"]).is_true()
    assert_that(mock_check.call_args.kwargs["ai_enabled"]).is_false()
    assert_that(mock_check.call_args.kwargs["paths"]).is_equal_to(["."])


def test_resolve_health_score_rejects_early_exit() -> None:
    """An unscored early-exit run must not become a 0/100 badge."""
    artifact = RunArtifact(early_exit=True, exit_code=1)

    with patch(
        "lintro.cli_utils.commands.badge.api.check_run",
        return_value=artifact,
    ):
        with pytest.raises(click.ClickException):
            resolve_health_score(score_override=None, paths=())


def test_badge_live_score_prints_markdown_only() -> None:
    """Without ``--score``, CLI output is the badge markdown (no leaked score)."""
    runner = CliRunner()
    artifact = _scored_artifact(score=88)

    def _fake_check_run(**_kwargs: object) -> RunArtifact:
        print("88")
        return artifact

    with patch(
        "lintro.cli_utils.commands.badge.api.check_run",
        side_effect=_fake_check_run,
    ):
        result = runner.invoke(badge_command, [])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output.strip()).is_equal_to(
        "![Lintro Score](https://img.shields.io/badge/lintro-88%2F100-brightgreen)",
    )
    assert_that(result.output).does_not_contain("88\n")


def test_badge_live_early_exit_prints_no_badge() -> None:
    """An early-exit live check exits non-zero and prints no shields snippet."""
    runner = CliRunner()
    artifact = RunArtifact(early_exit=True, exit_code=2)

    with patch(
        "lintro.cli_utils.commands.badge.api.check_run",
        return_value=artifact,
    ):
        result = runner.invoke(badge_command, [])

    assert_that(result.exit_code).is_not_equal_to(0)
    assert_that(result.output).does_not_contain("img.shields.io")


def test_badge_rejects_score_out_of_range() -> None:
    """Click rejects ``--score`` values outside 0-100."""
    runner = CliRunner()

    result = runner.invoke(badge_command, ["--score", "101"])

    assert_that(result.exit_code).is_not_equal_to(0)


def test_badge_rejects_json_and_url_together() -> None:
    """``--json`` and ``--url`` are mutually exclusive."""
    runner = CliRunner()

    result = runner.invoke(badge_command, ["--score", "84", "--json", "--url"])

    assert_that(result.exit_code).is_not_equal_to(0)
    assert_that(result.output.lower()).contains("not both")


def test_resolve_health_score_rejects_all_skipped_run() -> None:
    """An all-skipped run must not publish a perfect 100 badge."""
    artifact = RunArtifact(
        health=HealthScore(
            score=100,
            tier=ScoreTier.GREAT,
            counts=SeverityCounts(),
            weighted_penalty=0.0,
        ),
        tool_results=[
            ToolResult(
                name="ruff",
                success=True,
                skipped=True,
                skip_reason="no files matched",
            ),
        ],
    )

    with patch(
        "lintro.cli_utils.commands.badge.api.check_run",
        return_value=artifact,
    ):
        with pytest.raises(click.ClickException):
            resolve_health_score(score_override=None, paths=())


def test_resolve_health_score_rejects_no_files_found_run() -> None:
    """A run that executed but matched no files must not publish 100."""
    artifact = _scored_artifact(score=100)
    artifact.tool_results = [
        ToolResult(
            name="ruff",
            success=True,
            skipped=False,
            output="No .py/.pyi files found to check.",
        ),
        ToolResult(
            name="cargo_audit",
            success=True,
            skipped=False,
            output="No files found to check.",
        ),
        ToolResult(
            name="astro-check",
            success=True,
            skipped=False,
            output="No Astro files to check.",
        ),
    ]

    with patch(
        "lintro.cli_utils.commands.badge.api.check_run",
        return_value=artifact,
    ):
        with pytest.raises(click.ClickException):
            resolve_health_score(score_override=None, paths=())


def test_badge_empty_directory_does_not_publish(tmp_path: Path) -> None:
    """A real empty directory must not publish a 100/100 badge.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    runner = CliRunner()

    result = runner.invoke(badge_command, [str(tmp_path)])

    assert_that(result.exit_code).is_not_equal_to(0)
    assert_that(result.output).does_not_contain("img.shields.io")


def test_badge_live_no_files_found_prints_no_badge() -> None:
    """Empty-path live checks exit non-zero and print no shields snippet."""
    runner = CliRunner()
    artifact = _scored_artifact(score=100)
    artifact.tool_results = [
        ToolResult(
            name="ruff",
            success=True,
            skipped=False,
            output="No .py/.pyi files found to check.\nNo issues found.",
        ),
    ]

    with patch(
        "lintro.cli_utils.commands.badge.api.check_run",
        return_value=artifact,
    ):
        result = runner.invoke(badge_command, [])

    assert_that(result.exit_code).is_not_equal_to(0)
    assert_that(result.output).does_not_contain("img.shields.io")


def test_resolve_health_score_accepts_filter_empty_with_post_checks() -> None:
    """A filter-empty main phase is usable when a post-check inspected files."""
    artifact = _scored_artifact(score=88)
    artifact.main_phase_empty_due_to_filter = True

    with patch(
        "lintro.cli_utils.commands.badge.api.check_run",
        return_value=artifact,
    ):
        score = resolve_health_score(score_override=None, paths=())

    assert_that(score).is_equal_to(88)


def test_resolve_health_score_rejects_all_timeout_run() -> None:
    """An all-timeout run must not publish a perfect 100 badge."""
    artifact = _scored_artifact(score=100)
    artifact.tool_results = [
        ToolResult(
            name="ruff",
            success=False,
            skipped=False,
            timed_out=True,
            output="timed out after 30s",
        ),
    ]

    with patch(
        "lintro.cli_utils.commands.badge.api.check_run",
        return_value=artifact,
    ):
        with pytest.raises(click.ClickException):
            resolve_health_score(score_override=None, paths=())


def test_badge_live_all_skipped_prints_no_badge() -> None:
    """Skipped-only live checks exit non-zero and print no shields snippet."""
    runner = CliRunner()
    artifact = RunArtifact(
        health=HealthScore(
            score=100,
            tier=ScoreTier.GREAT,
            counts=SeverityCounts(),
            weighted_penalty=0.0,
        ),
        tool_results=[
            ToolResult(
                name="ruff",
                success=True,
                skipped=True,
                skip_reason="no files matched",
            ),
        ],
    )

    with patch(
        "lintro.cli_utils.commands.badge.api.check_run",
        return_value=artifact,
    ):
        result = runner.invoke(badge_command, [])

    assert_that(result.exit_code).is_not_equal_to(0)
    assert_that(result.output).does_not_contain("img.shields.io")


@pytest.mark.parametrize(
    "style",
    ["flat", "flat-square", "plastic", "for-the-badge", "social"],
)
def test_badge_style_query_keeps_hyphens(style: str) -> None:
    """Every supported shields style is appended without encoding hyphens.

    Args:
        style: shields.io style token from the CLI choice list.
    """
    runner = CliRunner()

    result = runner.invoke(
        badge_command,
        ["--score", "84", "--style", style, "--url"],
    )

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output.strip()).ends_with(f"?style={style}")
    assert_that(result.output).does_not_contain("%2D")


def test_badge_style_flat_square_keeps_hyphen() -> None:
    """Hyphenated shields styles are not percent-encoded."""
    runner = CliRunner()

    result = runner.invoke(
        badge_command,
        ["--score", "84", "--style", "flat-square", "--url"],
    )

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output.strip()).is_equal_to(
        "https://img.shields.io/badge/lintro-84%2F100-brightgreen?style=flat-square",
    )


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

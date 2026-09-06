"""Tests for the ``lintro badge`` CLI command."""

from __future__ import annotations

import json
from unittest.mock import patch

import click
import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli import cli
from lintro.cli_utils.commands.badge import (
    badge_command,
    resolve_severity_counts,
)
from lintro.models.core.run_artifact import RunArtifact
from lintro.models.core.severity_counts import SeverityCounts
from lintro.models.core.tool_result import ToolResult


def test_badge_markdown_default() -> None:
    """Default output is a markdown shields.io image for the issue counts."""
    runner = CliRunner()

    result = runner.invoke(badge_command, ["--errors", "0"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output.strip()).is_equal_to(
        "![Lintro Issues](https://img.shields.io/badge/lintro-0%20issues-brightgreen)",
    )


def test_badge_url_only() -> None:
    """``--url`` prints the bare shields.io URL."""
    runner = CliRunner()

    result = runner.invoke(badge_command, ["--warnings", "2", "--url"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output.strip()).is_equal_to(
        "https://img.shields.io/badge/lintro-2%20warnings-yellow",
    )


def test_badge_message_lists_each_severity_found() -> None:
    """A mixed run names every severity that has a non-zero count."""
    runner = CliRunner()

    result = runner.invoke(
        badge_command,
        ["--errors", "3", "--warnings", "1", "--info", "5", "--url"],
    )

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output.strip()).is_equal_to(
        "https://img.shields.io/badge/"
        "lintro-3%20errors%2C%201%20warning%2C%205%20info-red",
    )


def test_badge_style_flat() -> None:
    """``--style flat`` appends the shields style query parameter."""
    runner = CliRunner()

    result = runner.invoke(
        badge_command,
        ["--errors", "0", "--style", "flat", "--url"],
    )

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output.strip()).is_equal_to(
        "https://img.shields.io/badge/lintro-0%20issues-brightgreen?style=flat",
    )


def test_badge_json_output() -> None:
    """``--json`` emits the counts, message, color, url, and markdown."""
    runner = CliRunner()

    result = runner.invoke(badge_command, ["--errors", "4", "--json"])

    assert_that(result.exit_code).is_equal_to(0)
    payload = json.loads(result.output)
    assert_that(payload["counts"]).is_equal_to(
        {"error": 4, "warning": 0, "info": 0, "total": 4},
    )
    assert_that(payload["message"]).is_equal_to("4 errors")
    assert_that(payload["color"]).is_equal_to("red")
    assert_that(payload["url"]).is_equal_to(
        "https://img.shields.io/badge/lintro-4%20errors-red",
    )
    assert_that(payload["markdown"]).starts_with("![Lintro Issues](")


def test_badge_registered_on_cli() -> None:
    """The root CLI exposes the badge command help."""
    runner = CliRunner()

    result = runner.invoke(cli, ["badge", "--help"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output.lower()).contains("shields")


def test_resolve_severity_counts_uses_override() -> None:
    """An explicit override skips the live check API."""
    with patch("lintro.cli_utils.commands.badge.api.check_run") as mock_check:
        counts = resolve_severity_counts(
            override=SeverityCounts(errors=7),
            paths=(),
        )

    assert_that(counts.errors).is_equal_to(7)
    mock_check.assert_not_called()


def _counted_artifact(*, errors: int = 0, warnings: int = 0) -> RunArtifact:
    """Build a completed run artifact for badge tests.

    Args:
        errors: ERROR-severity count to embed on the artifact.
        warnings: WARNING-severity count to embed on the artifact.

    Returns:
        RunArtifact: Artifact carrying real severity counts.
    """
    return RunArtifact(
        severity_counts=SeverityCounts(errors=errors, warnings=warnings),
        tool_results=[
            ToolResult(name="ruff", success=True, skipped=False),
        ],
    )


def test_resolve_severity_counts_runs_check_when_needed() -> None:
    """Without an override, a quiet API check supplies the counts."""
    artifact = _counted_artifact(errors=2, warnings=1)

    with patch(
        "lintro.cli_utils.commands.badge.api.check_run",
        return_value=artifact,
    ) as mock_check:
        counts = resolve_severity_counts(override=None, paths=(".",))

    assert_that(counts.total).is_equal_to(3)
    mock_check.assert_called_once()
    assert_that(mock_check.call_args.kwargs["no_log"]).is_true()
    assert_that(mock_check.call_args.kwargs["ai_enabled"]).is_false()
    assert_that(mock_check.call_args.kwargs["paths"]).is_equal_to(["."])


def test_resolve_severity_counts_rejects_early_exit() -> None:
    """An early-exit run must not become a zero-issue badge."""
    artifact = RunArtifact(early_exit=True, exit_code=1)

    with patch(
        "lintro.cli_utils.commands.badge.api.check_run",
        return_value=artifact,
    ):
        with pytest.raises(click.ClickException):
            resolve_severity_counts(override=None, paths=())


def test_badge_live_run_prints_markdown_only() -> None:
    """Without an override, CLI output is only the badge markdown."""
    runner = CliRunner()
    artifact = _counted_artifact(errors=1)

    def _fake_check_run(**_kwargs: object) -> RunArtifact:
        print("noise from the run")
        return artifact

    with patch(
        "lintro.cli_utils.commands.badge.api.check_run",
        side_effect=_fake_check_run,
    ):
        result = runner.invoke(badge_command, [])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output.strip()).is_equal_to(
        "![Lintro Issues](https://img.shields.io/badge/lintro-1%20error-red)",
    )
    assert_that(result.output).does_not_contain("noise from the run")


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


def test_badge_rejects_negative_count() -> None:
    """Click rejects negative count overrides."""
    runner = CliRunner()

    result = runner.invoke(badge_command, ["--errors", "-1"])

    assert_that(result.exit_code).is_not_equal_to(0)


def test_badge_rejects_json_and_url_together() -> None:
    """``--json`` and ``--url`` are mutually exclusive."""
    runner = CliRunner()

    result = runner.invoke(badge_command, ["--errors", "0", "--json", "--url"])

    assert_that(result.exit_code).is_not_equal_to(0)
    assert_that(result.output.lower()).contains("not both")


def test_resolve_severity_counts_rejects_all_skipped_run() -> None:
    """An all-skipped run must not publish a zero-issue badge."""
    artifact = RunArtifact(
        severity_counts=SeverityCounts(),
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
            resolve_severity_counts(override=None, paths=())


def test_resolve_severity_counts_rejects_no_files_found_run() -> None:
    """A run that executed but matched no files must not publish a badge."""
    artifact = _counted_artifact()
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
            resolve_severity_counts(override=None, paths=())


def test_badge_live_no_files_found_prints_no_badge() -> None:
    """Empty-path live checks exit non-zero and print no shields snippet."""
    runner = CliRunner()
    artifact = _counted_artifact()
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


def test_resolve_severity_counts_accepts_filter_empty_with_post_checks() -> None:
    """A filter-empty main phase is usable when a post-check inspected files."""
    artifact = _counted_artifact(warnings=4)
    artifact.main_phase_empty_due_to_filter = True

    with patch(
        "lintro.cli_utils.commands.badge.api.check_run",
        return_value=artifact,
    ):
        counts = resolve_severity_counts(override=None, paths=())

    assert_that(counts.warnings).is_equal_to(4)


def test_resolve_severity_counts_rejects_all_timeout_run() -> None:
    """An all-timeout run must not publish a zero-issue badge."""
    artifact = _counted_artifact()
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
            resolve_severity_counts(override=None, paths=())


def test_badge_live_all_skipped_prints_no_badge() -> None:
    """Skipped-only live checks exit non-zero and print no shields snippet."""
    runner = CliRunner()
    artifact = RunArtifact(
        severity_counts=SeverityCounts(),
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
        ["--errors", "0", "--style", style, "--url"],
    )

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output.strip()).ends_with(f"?style={style}")
    assert_that(result.output).does_not_contain("%2D")


@pytest.mark.parametrize(
    ("args", "color_fragment"),
    [
        (["--errors", "0"], "brightgreen"),
        (["--info", "1"], "yellow"),
        (["--warnings", "3"], "yellow"),
        (["--errors", "1", "--warnings", "9"], "red"),
    ],
)
def test_badge_color_follows_the_worst_severity(
    args: list[str],
    color_fragment: str,
) -> None:
    """Badge color is green when clean, red on any error, else yellow.

    Args:
        args: Count override options passed to the CLI.
        color_fragment: Expected shields.io color token in the URL.
    """
    runner = CliRunner()

    result = runner.invoke(badge_command, [*args, "--url"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).contains(color_fragment)


def test_resolve_severity_counts_rejects_a_partial_timeout_run() -> None:
    """One timed-out tool disqualifies the whole badge, not just its counts.

    A completed clean tool alongside a timed-out security scanner would
    otherwise publish "0 issues" for findings that were never collected.
    """
    artifact = _counted_artifact()
    artifact.tool_results = [
        ToolResult(
            name="ruff",
            success=True,
            skipped=False,
            output="All checks passed",
        ),
        ToolResult(
            name="bandit",
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
        with pytest.raises(click.ClickException) as excinfo:
            resolve_severity_counts(override=None, paths=())

    assert_that(str(excinfo.value)).contains("timed out")


def test_badge_live_partial_timeout_prints_no_badge() -> None:
    """A partially timed-out live check exits non-zero and prints no badge."""
    runner = CliRunner()
    artifact = _counted_artifact()
    artifact.tool_results = [
        ToolResult(
            name="ruff",
            success=True,
            skipped=False,
            output="All checks passed",
        ),
        ToolResult(
            name="bandit",
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
        result = runner.invoke(badge_command, [])

    assert_that(result.exit_code).is_not_equal_to(0)
    assert_that(result.output).does_not_contain("img.shields.io")

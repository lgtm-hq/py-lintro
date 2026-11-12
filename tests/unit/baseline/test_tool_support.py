"""Default and mixed selections preserve uncertified producer failures."""

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli import cli
from lintro.models.core.run_artifact import RunArtifact
from lintro.models.core.tool_result import ToolResult


@pytest.mark.parametrize(
    ("state", "reason"),
    [
        ({"inspected_files": None}, "file inspection was not confirmed"),
        ({"timed_out": True, "inspected_files": False}, "execution timed out"),
        ({"execution_failed": True}, "tool execution failed"),
        ({"parse_failures_count": 2}, "report has 2 parse failure(s)"),
        ({"issues_count": 2}, "finding count does not match the parsed report"),
        (
            {"residual_unknown": True, "residual_unknown_reason": "verify failed"},
            "residual findings are unknown",
        ),
        (
            {"issues": [], "issues_count": 0},
            "failed verdict is not explained by findings",
        ),
    ],
)
def test_management_names_every_blocker_without_dropping_failed_tool(
    workspace: Path,
    dirty_result: ToolResult,
    monkeypatch: pytest.MonkeyPatch,
    state: dict[str, Any],
    reason: str,
) -> None:
    """A complete peer never allows incomplete or uncertified results to be skipped."""
    monkeypatch.setattr(
        "lintro.cli_utils.commands.baseline.execute_run",
        lambda **_: RunArtifact(
            workspace_root=workspace,
            tool_results=[
                dirty_result,
                replace(dirty_result, name="mypy", findings_complete=False),
                replace(dirty_result, name="black", **state),
            ],
        ),
    )

    result = CliRunner().invoke(cli, ["baseline", "create"])

    assert_that(result.exit_code).is_equal_to(1)
    assert_that(result.output).contains(
        "mypy: producer does not certify complete findings for baselining",
        f"black: {reason}",
    )
    assert_that((workspace / ".lintro-baseline.json").exists()).is_false()


@pytest.mark.parametrize("subcommand", [[], ["create"], ["update"], ["prune"]])
def test_management_help_documents_certified_tools_and_mixed_selection(
    subcommand: list[str],
) -> None:
    """Each management entry point exposes the supported producer list and limits."""
    result = CliRunner().invoke(cli, ["baseline", *subcommand, "--help"])
    text = " ".join(result.output.split())

    assert_that(result.exit_code).is_zero()
    assert_that(text).contains(
        "Certified check findings: ruff, black.",
        "Default or mixed selections abort",
        "no failing tool is silently omitted",
    )


@pytest.mark.parametrize("command", ["chk", "fmt"])
def test_baseline_option_help_explains_unsupported_findings_remain_failing(
    command: str,
) -> None:
    """Filtering help points users to the certified tool list and preserves limits."""
    result = CliRunner().invoke(cli, [command, "--help"])
    text = " ".join(result.output.split())

    assert_that(result.exit_code).is_zero()
    assert_that(text).contains(
        "Unsupported or incomplete findings remain visible and failing",
        "lintro baseline --help",
    )

"""Tests for ``--explain-order`` on check and format (#1741, #1742).

The flag is reporting only: it prints the order the run would use and
returns without handing anything to the execution pipeline.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

import click
import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli_utils.commands.check import check_command
from lintro.cli_utils.commands.format import format_command


def test_check_explain_order_prints_diff_and_runs_nothing() -> None:
    """``lintro check --explain-order`` reports and exits without running."""
    runner = CliRunner()

    args = ["--tools", "ruff,black", "--explain-order"]

    with patch("lintro.cli_utils.commands.check.run_lint_with_ai") as mock_run:
        result = runner.invoke(check_command, args)

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(mock_run.called).is_false()
    assert_that(result.output).contains("Execution order (derived from tool claims)")
    assert_that(result.output).contains("1. ruff")
    assert_that(result.output).contains("2. black")
    assert_that(result.output).contains("after ruff")


def test_format_explain_order_prints_diff_and_runs_nothing() -> None:
    """``lintro fmt --explain-order`` reports and exits without formatting."""
    runner = CliRunner()

    with patch("lintro.cli_utils.commands.format.run_lint_with_ai") as mock_run:
        result = runner.invoke(
            format_command,
            ["--tools", "ruff,black", "--explain-order"],
        )

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(mock_run.called).is_false()
    assert_that(result.output).contains("Execution order (derived from tool claims)")
    assert_that(result.output).contains("*.py: ruff(fix) -> black(format)")


def test_check_explain_order_rejects_an_unknown_tool() -> None:
    """An unknown ``--tools`` value exits 1 instead of printing a diff."""
    runner = CliRunner()

    with patch("lintro.cli_utils.commands.check.run_lint_with_ai") as mock_run:
        result = runner.invoke(
            check_command,
            ["--tools", "definitely-not-a-tool", "--explain-order"],
        )

    assert_that(result.exit_code).is_equal_to(1)
    assert_that(mock_run.called).is_false()


def test_check_without_explain_order_still_runs() -> None:
    """Omitting the flag leaves the normal pipeline untouched."""
    runner = CliRunner()

    with patch(
        "lintro.cli_utils.commands.check.run_lint_with_ai",
        return_value=0,
    ) as mock_run:
        result = runner.invoke(check_command, ["--tools", "ruff"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(mock_run.called).is_true()
    assert_that(result.output).does_not_contain(
        "Execution order (derived from tool claims)",
    )


def test_check_explain_order_still_validates_the_diff_base() -> None:
    """``--diff`` validation runs before the explain-order early exit."""
    runner = CliRunner()
    args = ["--diff", ".", "--tools", "ruff", "--explain-order"]

    with patch("lintro.cli_utils.commands.check.run_lint_with_ai") as mock_run:
        result = runner.invoke(check_command, args)

    assert_that(result.exit_code).is_not_equal_to(0)
    assert_that(mock_run.called).is_false()
    assert_that(result.output).does_not_contain(
        "Execution order (derived from tool claims)",
    )


def test_check_no_cache_still_clears_before_explain_order() -> None:
    """``--no-cache`` clears the caches even when the run only explains."""
    runner = CliRunner()
    args = ["--tools", "ruff", "--no-cache", "--explain-order"]

    with (
        patch("lintro.cli_utils.commands.check.run_lint_with_ai") as mock_run,
        patch("lintro.utils.file_cache.clear_all_caches") as mock_clear,
    ):
        result = runner.invoke(check_command, args)

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(mock_run.called).is_false()
    assert_that(mock_clear.called).is_true()
    assert_that(result.output).contains("Execution order (derived from tool claims)")


def test_check_explain_order_uses_the_default_selection() -> None:
    """Without ``--tools`` the order covers the detected default toolset."""
    runner = CliRunner()

    with patch("lintro.cli_utils.commands.check.run_lint_with_ai") as mock_run:
        result = runner.invoke(check_command, ["--explain-order"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(mock_run.called).is_false()
    assert_that(result.output).contains("Execution order (derived from tool claims)")
    assert_that(result.output).contains("ruff")


@pytest.mark.parametrize(
    "command",
    [check_command, format_command],
    ids=["check", "format"],
)
@pytest.mark.parametrize(
    "ignore",
    [False, True],
    ids=["resolve-conflicts", "ignore-conflicts"],
)
def test_explained_selection_matches_execution_when_conflicts_filter_tools(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: click.Command,
    ignore: bool,
) -> None:
    """Explanations and actual runs honor the same conflict-selection switch.

    Args:
        tmp_path: Isolated fixture project.
        monkeypatch: Conditional conflict resolver and native tool doubles.
        command: Check or format command to exercise end to end.
        ignore: Whether both selected tools should survive conflict resolution.
    """
    from lintro.config.config_loader import clear_config_cache
    from lintro.models.core.tool_result import ToolResult
    from lintro.models.core.tool_snapshot import ToolSnapshot
    from lintro.plugins.base import BaseToolPlugin
    from lintro.tools import tool_manager
    from lintro.tools.black.definition import BlackPlugin
    from lintro.tools.ruff.definition import RuffPlugin

    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / ".lintro-config.yaml").write_text(
        "execution:\n  parallel: false\nai:\n  enabled: false\n",
    )
    clear_config_cache()
    executed: list[str] = []

    def order(names: list[str], ignore_conflicts: bool = False) -> list[str]:
        """Model a conflict resolver that can remove a selected tool.

        Args:
            names: Candidate tools.
            ignore_conflicts: Preserve both candidates only when requested.

        Returns:
            The resolver's selected subset.
        """
        return (
            list(names)
            if ignore_conflicts
            else [name for name in names if name != "black"]
        )

    def run(
        self: BaseToolPlugin,
        paths: list[str],
        options: dict[str, object],
    ) -> ToolResult:
        """Record tools actually executed by the native pipeline.

        Args:
            self: Plugin being invoked.
            paths: Requested paths.
            options: Runtime overrides.

        Returns:
            A successful empty native result.
        """
        executed.append(str(self.name))
        return ToolResult(name=self.name, success=True, inspected_files=True)

    monkeypatch.setattr(tool_manager, "get_tool_execution_order", order)
    monkeypatch.setattr(
        "lintro.utils.tool_snapshots.get_tool_snapshot",
        lambda name, **kwargs: ToolSnapshot(
            name=name,
            available=True,
            version_check_passed=True,
        ),
    )
    for plugin in (RuffPlugin, BlackPlugin):
        monkeypatch.setattr(plugin, "check", run)
        monkeypatch.setattr(plugin, "fix", run)
    args = [
        ".",
        "--tools",
        "ruff,black",
        "--yes",
        "--tool-options",
        "ruff:format=True,ruff:format_check=True",
    ]
    if ignore:
        args.append("--ignore-conflicts")
    runner = CliRunner()
    explanation = runner.invoke(command, [*args, "--explain-order"])
    actual = runner.invoke(command, args)
    assert_that(explanation.exit_code).is_equal_to(0)
    assert_that(actual.exit_code).is_equal_to(0)
    expected = {"ruff", "black"} if ignore else {"ruff"}
    assert_that(set(executed)).is_equal_to(expected)
    explained = set(
        re.findall(r"^\s+\d+\. ([\w-]+)$", explanation.output, flags=re.MULTILINE),
    )
    assert_that(explained).is_equal_to(set(executed))
    if ignore:
        assert_that(explanation.output).contains("explicitly enabled")

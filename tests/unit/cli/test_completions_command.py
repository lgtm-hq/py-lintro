"""Tests for the shell completions CLI command."""

from __future__ import annotations

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli import cli


@pytest.mark.parametrize(
    ("shell", "expected_snippets"),
    [
        (
            "bash",
            (
                "_lintro_completion()",
                "_LINTRO_COMPLETE=bash_complete",
                "complete -o nosort -F _lintro_completion lintro",
            ),
        ),
        (
            "zsh",
            (
                "#compdef lintro",
                "_lintro_completion()",
                "_LINTRO_COMPLETE=zsh_complete",
            ),
        ),
        (
            "fish",
            (
                "function _lintro_completion",
                "_LINTRO_COMPLETE=fish_complete",
                "complete --no-files --command lintro",
            ),
        ),
    ],
)
def test_completions_command_outputs_click_source_for_shell(
    isolated_cli_runner: CliRunner,
    shell: str,
    expected_snippets: tuple[str, ...],
) -> None:
    """The completions command emits Click's source script for each shell."""
    result = isolated_cli_runner.invoke(cli, ["completions", shell])

    assert_that(result.exit_code).is_equal_to(0)
    for snippet in expected_snippets:
        assert_that(result.output).contains(snippet)


def test_completions_alias_outputs_same_script(
    isolated_cli_runner: CliRunner,
) -> None:
    """The comp alias mirrors the completions command."""
    canonical = isolated_cli_runner.invoke(cli, ["completions", "bash"])
    alias = isolated_cli_runner.invoke(cli, ["comp", "bash"])

    assert_that(canonical.exit_code).is_equal_to(0)
    assert_that(alias.exit_code).is_equal_to(0)
    assert_that(alias.output).is_equal_to(canonical.output)


def test_completions_command_rejects_invalid_shell(
    isolated_cli_runner: CliRunner,
) -> None:
    """Invalid shells fail through Click argument validation."""
    result = isolated_cli_runner.invoke(cli, ["completions", "powershell"])
    error_output = result.output + result.stderr

    assert_that(result.exit_code).is_equal_to(2)
    assert_that(error_output).contains("Invalid value")
    assert_that(error_output).contains("powershell")
    assert_that(error_output).contains("bash")
    assert_that(error_output).contains("zsh")
    assert_that(error_output).contains("fish")

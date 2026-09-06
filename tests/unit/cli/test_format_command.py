"""Unit tests for lintro/cli_utils/commands/format.py."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli_utils.commands.format import format_code, format_command
from tests.unit.cli.conftest import RecordedLintRun

# =============================================================================
# Format Command Basic Tests
# =============================================================================


def test_format_command_help(cli_runner: CliRunner) -> None:
    """Verify format command shows help.

    Args:
        cli_runner: The Click CLI test runner.
    """
    result = cli_runner.invoke(format_command, ["--help"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).contains("Format")
    assert_that(result.output).contains("language-detected")


def test_format_command_default_paths(
    cli_runner: CliRunner,
    recorded_format_run: RecordedLintRun,
) -> None:
    """Verify format command uses default paths when none provided.

    Args:
        cli_runner: The Click CLI test runner.
        recorded_format_run: Recorder standing in for the lint pipeline.
    """
    with cli_runner.isolated_filesystem():
        result = cli_runner.invoke(format_command, [])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(recorded_format_run.calls).is_length(1)
    call_kwargs = recorded_format_run.calls[0]
    assert_that(call_kwargs["paths"]).is_equal_to(["."])


def test_format_command_with_paths(
    cli_runner: CliRunner,
    recorded_format_run: RecordedLintRun,
    tmp_path: Path,
) -> None:
    """Verify format command passes provided paths.

    Args:
        cli_runner: The Click CLI test runner.
        recorded_format_run: Recorder standing in for the lint pipeline.
        tmp_path: Temporary directory path for testing.
    """
    test_file = tmp_path / "test.py"
    test_file.write_text("# test")

    result = cli_runner.invoke(format_command, [str(test_file)])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(recorded_format_run.calls).is_length(1)
    call_kwargs = recorded_format_run.calls[0]
    assert_that(call_kwargs["paths"]).contains(str(test_file))


def test_format_command_exit_code_zero_on_success(
    cli_runner: CliRunner,
    recorded_format_run: RecordedLintRun,
) -> None:
    """Verify format command exits with 0 on success.

    Args:
        cli_runner: The Click CLI test runner.
        recorded_format_run: Recorder standing in for the lint pipeline.
    """
    recorded_format_run.exit_code = 0
    with cli_runner.isolated_filesystem():
        result = cli_runner.invoke(format_command, [])

    assert_that(result.exit_code).is_equal_to(0)


def test_format_command_exit_code_nonzero_on_error(
    cli_runner: CliRunner,
    recorded_format_run: RecordedLintRun,
) -> None:
    """Verify format command exits with non-zero on error.

    Args:
        cli_runner: The Click CLI test runner.
        recorded_format_run: Recorder standing in for the lint pipeline.
    """
    recorded_format_run.exit_code = 1
    with cli_runner.isolated_filesystem():
        result = cli_runner.invoke(format_command, [])

    assert_that(result.exit_code).is_equal_to(1)


# =============================================================================
# Format Command Options Tests
# =============================================================================


def test_format_command_tools_option(
    cli_runner: CliRunner,
    recorded_format_run: RecordedLintRun,
) -> None:
    """Verify --tools option is passed correctly.

    Args:
        cli_runner: The Click CLI test runner.
        recorded_format_run: Recorder standing in for the lint pipeline.
    """
    with cli_runner.isolated_filesystem():
        result = cli_runner.invoke(format_command, ["--tools", "ruff,black"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(recorded_format_run.calls).is_length(1)
    call_kwargs = recorded_format_run.calls[0]
    assert_that(call_kwargs["tools"]).is_equal_to("ruff,black")


def test_format_command_exclude_option(
    cli_runner: CliRunner,
    recorded_format_run: RecordedLintRun,
) -> None:
    """Verify --exclude option is passed correctly.

    Args:
        cli_runner: The Click CLI test runner.
        recorded_format_run: Recorder standing in for the lint pipeline.
    """
    with cli_runner.isolated_filesystem():
        result = cli_runner.invoke(format_command, ["--exclude", "*.pyc,__pycache__"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(recorded_format_run.calls).is_length(1)
    call_kwargs = recorded_format_run.calls[0]
    assert_that(call_kwargs["exclude"]).is_equal_to("*.pyc,__pycache__")


def test_format_command_include_venv_flag(
    cli_runner: CliRunner,
    recorded_format_run: RecordedLintRun,
) -> None:
    """Verify --include-venv flag is passed correctly.

    Args:
        cli_runner: The Click CLI test runner.
        recorded_format_run: Recorder standing in for the lint pipeline.
    """
    with cli_runner.isolated_filesystem():
        result = cli_runner.invoke(format_command, ["--include-venv"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(recorded_format_run.calls).is_length(1)
    call_kwargs = recorded_format_run.calls[0]
    assert_that(call_kwargs["include_venv"]).is_true()


def test_format_command_output_format_option(
    cli_runner: CliRunner,
    recorded_format_run: RecordedLintRun,
) -> None:
    """Verify --output-format option is passed correctly.

    Args:
        cli_runner: The Click CLI test runner.
        recorded_format_run: Recorder standing in for the lint pipeline.
    """
    with cli_runner.isolated_filesystem():
        result = cli_runner.invoke(format_command, ["--output-format", "json"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(recorded_format_run.calls).is_length(1)
    call_kwargs = recorded_format_run.calls[0]
    assert_that(call_kwargs["output_format"]).is_equal_to("json")


@pytest.mark.parametrize(
    "format_option",
    ["plain", "grid", "markdown", "html", "json", "csv"],
    ids=["plain", "grid", "markdown", "html", "json", "csv"],
)
def test_format_command_output_format_valid_choices(
    cli_runner: CliRunner,
    recorded_format_run: RecordedLintRun,
    format_option: str,
) -> None:
    """Verify all valid output format choices are accepted.

    Args:
        cli_runner: The Click CLI test runner.
        recorded_format_run: Recorder standing in for the lint pipeline.
        format_option: The output format option being tested.
    """
    with cli_runner.isolated_filesystem():
        result = cli_runner.invoke(format_command, ["--output-format", format_option])

    assert_that(result.exit_code).is_equal_to(0)


def test_format_command_group_by_option(
    cli_runner: CliRunner,
    recorded_format_run: RecordedLintRun,
) -> None:
    """Verify --group-by option is passed correctly.

    Args:
        cli_runner: The Click CLI test runner.
        recorded_format_run: Recorder standing in for the lint pipeline.
    """
    with cli_runner.isolated_filesystem():
        result = cli_runner.invoke(format_command, ["--group-by", "code"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(recorded_format_run.calls).is_length(1)
    call_kwargs = recorded_format_run.calls[0]
    assert_that(call_kwargs["group_by"]).is_equal_to("code")


def test_format_command_group_by_category_is_forwarded(
    cli_runner: CliRunner,
    recorded_format_run: RecordedLintRun,
) -> None:
    """Verify --group-by category is passed through to the executor.

    Args:
        cli_runner: The Click CLI test runner.
        recorded_format_run: Recorder standing in for the lint pipeline.
    """
    with cli_runner.isolated_filesystem():
        result = cli_runner.invoke(format_command, ["--group-by", "category"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(recorded_format_run.calls).is_length(1)
    call_kwargs = recorded_format_run.calls[0]
    assert_that(call_kwargs["group_by"]).is_equal_to("category")


@pytest.mark.parametrize(
    "group_by_option",
    ["file", "code", "none", "auto", "category"],
    ids=["file", "code", "none", "auto", "category"],
)
def test_format_command_group_by_valid_choices(
    cli_runner: CliRunner,
    recorded_format_run: RecordedLintRun,
    group_by_option: str,
) -> None:
    """Verify all valid group-by choices are accepted.

    Args:
        cli_runner: The Click CLI test runner.
        recorded_format_run: Recorder standing in for the lint pipeline.
        group_by_option: The group-by option being tested.
    """
    with cli_runner.isolated_filesystem():
        result = cli_runner.invoke(format_command, ["--group-by", group_by_option])

    assert_that(result.exit_code).is_equal_to(0)


def test_format_command_verbose_flag(
    cli_runner: CliRunner,
    recorded_format_run: RecordedLintRun,
) -> None:
    """Verify --verbose flag is passed correctly.

    Args:
        cli_runner: The Click CLI test runner.
        recorded_format_run: Recorder standing in for the lint pipeline.
    """
    with cli_runner.isolated_filesystem():
        result = cli_runner.invoke(format_command, ["--verbose"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(recorded_format_run.calls).is_length(1)
    call_kwargs = recorded_format_run.calls[0]
    assert_that(call_kwargs["verbose"]).is_true()


def test_format_command_verbose_short_flag(
    cli_runner: CliRunner,
    recorded_format_run: RecordedLintRun,
) -> None:
    """Verify -v short flag works for verbose.

    Args:
        cli_runner: The Click CLI test runner.
        recorded_format_run: Recorder standing in for the lint pipeline.
    """
    with cli_runner.isolated_filesystem():
        result = cli_runner.invoke(format_command, ["-v"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(recorded_format_run.calls).is_length(1)
    call_kwargs = recorded_format_run.calls[0]
    assert_that(call_kwargs["verbose"]).is_true()


def test_format_command_raw_output_flag(
    cli_runner: CliRunner,
    recorded_format_run: RecordedLintRun,
) -> None:
    """Verify --raw-output flag is passed correctly.

    Args:
        cli_runner: The Click CLI test runner.
        recorded_format_run: Recorder standing in for the lint pipeline.
    """
    with cli_runner.isolated_filesystem():
        result = cli_runner.invoke(format_command, ["--raw-output"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(recorded_format_run.calls).is_length(1)
    call_kwargs = recorded_format_run.calls[0]
    assert_that(call_kwargs["raw_output"]).is_true()


def test_format_command_tool_options(
    cli_runner: CliRunner,
    recorded_format_run: RecordedLintRun,
) -> None:
    """Verify --tool-options is passed correctly.

    Args:
        cli_runner: The Click CLI test runner.
        recorded_format_run: Recorder standing in for the lint pipeline.
    """
    with cli_runner.isolated_filesystem():
        result = cli_runner.invoke(
            format_command,
            ["--tool-options", "ruff:line-length=120"],
        )

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(recorded_format_run.calls).is_length(1)
    call_kwargs = recorded_format_run.calls[0]
    assert_that(call_kwargs["tool_options"]).is_equal_to("ruff:line-length=120")


def test_format_command_uses_fmt_action(
    cli_runner: CliRunner,
    recorded_format_run: RecordedLintRun,
) -> None:
    """Verify format command uses 'fmt' action.

    Args:
        cli_runner: The Click CLI test runner.
        recorded_format_run: Recorder standing in for the lint pipeline.
    """
    with cli_runner.isolated_filesystem():
        result = cli_runner.invoke(format_command, [])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(recorded_format_run.calls).is_length(1)
    call_kwargs = recorded_format_run.calls[0]
    assert_that(call_kwargs["action"]).is_equal_to("fmt")


# =============================================================================
# Programmatic format_code() Function Tests
# =============================================================================


def test_format_code_function_raises_on_failure() -> None:
    """Verify format_code() function raises RuntimeError on failure."""
    with patch("lintro.api.core.run_lint_with_ai", return_value=1):
        with pytest.raises(RuntimeError) as exc_info:
            format_code(
                paths=["src"],
                tools="ruff",
            )

        assert_that(str(exc_info.value)).contains("Format failed")


def test_format_code_function_default_parameters(tmp_path: Path) -> None:
    """format_code() reformats a file with every option left at its default.

    Passing no ``tools`` selects the whole formatter set, so the badly spaced
    assignment below is rewritten in place. The file content afterwards is the
    observable proof that the defaults produce a real run.

    Args:
        tmp_path: Pytest temporary directory holding the file to format.
    """
    target = tmp_path / "dirty.py"
    target.write_text('"""Module docstring."""\n\nVALUE   =   1\n', encoding="utf-8")

    format_code(paths=[str(tmp_path)], yes=True)

    assert_that(target.read_text(encoding="utf-8")).is_equal_to(
        '"""Module docstring."""\n\nVALUE = 1\n',
    )


def test_format_code_function_with_all_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify format_code() passes all options correctly.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to install the recorder.
    """
    calls: list[dict[str, Any]] = []

    def _record(**kwargs: Any) -> int:
        calls.append(dict(kwargs))
        return 0

    monkeypatch.setattr("lintro.api.core.run_lint_with_ai", _record)

    format_code(
        paths=["src", "tests"],
        tools="ruff,black",
        tool_options="ruff:line-length=100",
        exclude="*.pyc",
        include_venv=True,
        group_by="file",
        output_format="json",
        verbose=True,
    )

    assert_that(calls).is_length(1)
    assert_that(calls[0]["paths"]).contains("src")
    assert_that(calls[0]["paths"]).contains("tests")
    assert_that(calls[0]["tools"]).is_equal_to("ruff,black")
    assert_that(calls[0]["tool_options"]).is_equal_to("ruff:line-length=100")
    assert_that(calls[0]["exclude"]).is_equal_to("*.pyc")
    assert_that(calls[0]["group_by"]).is_equal_to("file")
    assert_that(calls[0]["output_format"]).is_equal_to("json")
    assert_that(calls[0]["include_venv"]).is_true()
    assert_that(calls[0]["verbose"]).is_true()


# =============================================================================
# Format Command Edge Cases
# =============================================================================


def test_format_command_invalid_output_format(cli_runner: CliRunner) -> None:
    """Verify format command rejects invalid output format.

    Args:
        cli_runner: The Click CLI test runner.
    """
    with cli_runner.isolated_filesystem():
        result = cli_runner.invoke(format_command, ["--output-format", "invalid"])

    assert_that(result.exit_code).is_not_equal_to(0)
    assert_that(result.output).contains("Invalid value")


def test_format_command_invalid_group_by(cli_runner: CliRunner) -> None:
    """Verify format command rejects invalid group-by option.

    Args:
        cli_runner: The Click CLI test runner.
    """
    with cli_runner.isolated_filesystem():
        result = cli_runner.invoke(format_command, ["--group-by", "invalid"])

    assert_that(result.exit_code).is_not_equal_to(0)
    assert_that(result.output).contains("Invalid value")


def test_format_command_multiple_paths(
    cli_runner: CliRunner,
    recorded_format_run: RecordedLintRun,
    tmp_path: Path,
) -> None:
    """Verify format command handles multiple paths.

    Args:
        cli_runner: The Click CLI test runner.
        recorded_format_run: Recorder standing in for the lint pipeline.
        tmp_path: Temporary directory path for testing.
    """
    file1 = tmp_path / "file1.py"
    file2 = tmp_path / "file2.py"
    file1.write_text("# file1")
    file2.write_text("# file2")

    result = cli_runner.invoke(format_command, [str(file1), str(file2)])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(recorded_format_run.calls).is_length(1)
    call_kwargs = recorded_format_run.calls[0]
    assert_that(len(call_kwargs["paths"])).is_equal_to(2)

"""Unit tests for the pytest introspection flags on ``lintro test``.

The command's job for these flags is to normalise them into the prefixed
``pytest:`` tool-option string the pipeline consumes. Each test reads that
string out of a plain recording stand-in together with the command's exit
code, so no assertion here inspects mock call bookkeeping (#2315).
"""

from __future__ import annotations

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli_utils.commands.test import test_command as pytest_cli_command
from tests.unit.pytest.conftest import PipelineRecorder


@pytest.mark.parametrize(
    ("argv", "expected_option"),
    [
        (["--collect-only"], "pytest:collect_only=True"),
        (["--fixtures"], "pytest:list_fixtures=True"),
        (["--fixture-info", "sample_data"], "pytest:fixture_info=sample_data"),
        (["--markers"], "pytest:list_markers=True"),
        (["--parametrize-help"], "pytest:parametrize_help=True"),
    ],
    ids=[
        "collect-only",
        "fixtures",
        "fixture-info",
        "markers",
        "parametrize-help",
    ],
)
def test_introspection_flag_becomes_a_prefixed_tool_option(
    recorded_pipeline: PipelineRecorder,
    argv: list[str],
    expected_option: str,
) -> None:
    """Each introspection flag reaches pytest as a prefixed tool option.

    Args:
        recorded_pipeline: Recorder for the pipeline the command drives.
        argv: Command-line arguments to invoke the command with.
        expected_option: Tool option the flag is expected to produce.
    """
    result = CliRunner().invoke(pytest_cli_command, argv)

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(recorded_pipeline.only_run["tool_options"]).contains(expected_option)


def test_test_command_coverage_options(recorded_pipeline: PipelineRecorder) -> None:
    """Already-prefixed coverage options are forwarded untouched.

    Args:
        recorded_pipeline: Recorder for the pipeline the command drives.
    """
    result = CliRunner().invoke(
        pytest_cli_command,
        [
            "--tool-options",
            "pytest:coverage_html=htmlcov,pytest:coverage_xml=coverage.xml",
        ],
    )

    assert_that(result.exit_code).is_equal_to(0)
    tool_options = recorded_pipeline.only_run["tool_options"]
    assert_that(tool_options).contains("pytest:coverage_html=htmlcov")
    assert_that(tool_options).contains("pytest:coverage_xml=coverage.xml")


def test_test_command_multiple_new_flags(recorded_pipeline: PipelineRecorder) -> None:
    """Several introspection flags combine into one tool-option string.

    Args:
        recorded_pipeline: Recorder for the pipeline the command drives.
    """
    result = CliRunner().invoke(
        pytest_cli_command,
        ["--list-plugins", "--markers", "--collect-only"],
    )

    assert_that(result.exit_code).is_equal_to(0)
    tool_options = recorded_pipeline.only_run["tool_options"]
    assert_that(tool_options).contains("pytest:list_plugins=True")
    assert_that(tool_options).contains("pytest:list_markers=True")
    assert_that(tool_options).contains("pytest:collect_only=True")


def test_test_command_tool_options_without_prefix(
    recorded_pipeline: PipelineRecorder,
) -> None:
    """Bare tool options gain the ``pytest:`` prefix.

    Args:
        recorded_pipeline: Recorder for the pipeline the command drives.
    """
    result = CliRunner().invoke(
        pytest_cli_command,
        ["--tool-options", "verbose=true,tb=long"],
    )

    assert_that(result.exit_code).is_equal_to(0)
    tool_options = recorded_pipeline.only_run["tool_options"]
    assert_that(tool_options).contains("pytest:verbose=true")
    assert_that(tool_options).contains("pytest:tb=long")


def test_test_command_tool_options_with_prefix(
    recorded_pipeline: PipelineRecorder,
) -> None:
    """An already-prefixed tool option is not prefixed twice.

    Args:
        recorded_pipeline: Recorder for the pipeline the command drives.
    """
    result = CliRunner().invoke(
        pytest_cli_command,
        ["--tool-options", "pytest:verbose=true"],
    )

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(recorded_pipeline.only_run["tool_options"]).is_equal_to(
        "pytest:verbose=true",
    )


def test_test_command_tool_options_mixed(
    recorded_pipeline: PipelineRecorder,
) -> None:
    """Mixing prefixed and bare tool options prefixes only the bare ones.

    Args:
        recorded_pipeline: Recorder for the pipeline the command drives.
    """
    result = CliRunner().invoke(
        pytest_cli_command,
        ["--tool-options", "verbose=true,pytest:tb=long"],
    )

    assert_that(result.exit_code).is_equal_to(0)
    tool_options = recorded_pipeline.only_run["tool_options"]
    assert_that(tool_options).contains("pytest:verbose=true")
    assert_that(tool_options).contains("pytest:tb=long")
    assert_that(tool_options).does_not_contain("pytest:pytest:")


def test_test_command_exit_code_success(recorded_pipeline: PipelineRecorder) -> None:
    """Test test command propagates success exit code.

    Args:
        recorded_pipeline: Recorder for the pipeline the command drives.
    """
    recorded_pipeline.exit_code = 0

    result = CliRunner().invoke(pytest_cli_command, [])

    assert_that(result.exit_code).is_equal_to(0)


def test_test_command_exit_code_failure(recorded_pipeline: PipelineRecorder) -> None:
    """Test test command propagates failure exit code.

    Args:
        recorded_pipeline: Recorder for the pipeline the command drives.
    """
    recorded_pipeline.exit_code = 1

    result = CliRunner().invoke(pytest_cli_command, [])

    assert_that(result.exit_code).is_equal_to(1)


def test_test_command_combined_options(recorded_pipeline: PipelineRecorder) -> None:
    """Every option on one command line reaches the pipeline together.

    Args:
        recorded_pipeline: Recorder for the pipeline the command drives.
    """
    result = CliRunner().invoke(
        pytest_cli_command,
        [
            ".",
            "--exclude",
            "*.venv",
            "--include-venv",
            "--output-format",
            "markdown",
            "--group-by",
            "file",
            "--verbose",
            "--raw-output",
            "--tool-options",
            "maxfail=5",
        ],
    )

    assert_that(result.exit_code).is_equal_to(0)
    run = recorded_pipeline.only_run
    assert_that(run["exclude"]).is_equal_to("*.venv")
    assert_that(run["include_venv"]).is_true()
    assert_that(run["output_format"]).is_equal_to("markdown")
    assert_that(run["group_by"]).is_equal_to("file")
    assert_that(run["verbose"]).is_true()
    assert_that(run["raw_output"]).is_true()
    assert_that(run["tool_options"]).contains("pytest:maxfail=5")

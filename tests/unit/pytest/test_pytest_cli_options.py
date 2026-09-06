"""Tests for the ``lintro test`` CLI option surface.

Each test drives the real Click command and reads the keyword arguments the
command handed to the pipeline out of a plain recording stand-in, together
with the exit code the command produced. Nothing here asserts on mock call
bookkeeping (#2315).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli_utils.commands.test import test_command as pytest_cli_command
from tests.unit.pytest.conftest import PipelineRecorder


def test_test_command_help() -> None:
    """Test that test command shows help."""
    runner = CliRunner()
    result = runner.invoke(pytest_cli_command, ["--help"])
    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).contains("Run tests using pytest")


def test_test_command_default_paths(recorded_pipeline: PipelineRecorder) -> None:
    """A bare invocation tests the current directory with the pytest tool.

    Args:
        recorded_pipeline: Recorder for the pipeline the command drives.
    """
    result = CliRunner().invoke(pytest_cli_command, [])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(recorded_pipeline.only_run["paths"]).is_equal_to(["."])
    assert_that(recorded_pipeline.only_run["tools"]).is_equal_to("pytest")


def test_test_command_explicit_paths(
    recorded_pipeline: PipelineRecorder,
    tmp_path: Path,
) -> None:
    """An explicit path reaches the pipeline instead of the default.

    Args:
        recorded_pipeline: Recorder for the pipeline the command drives.
        tmp_path: Pytest temporary directory for the generated test module.
    """
    target = tmp_path / "test_file.py"
    target.write_text("def test_generated() -> None:\n    pass\n", encoding="utf-8")

    result = CliRunner().invoke(pytest_cli_command, [str(target)])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(recorded_pipeline.only_run["paths"]).contains(str(target))


def test_test_command_exclude_patterns(recorded_pipeline: PipelineRecorder) -> None:
    """``--exclude`` forwards its comma-separated patterns unchanged.

    Args:
        recorded_pipeline: Recorder for the pipeline the command drives.
    """
    result = CliRunner().invoke(
        pytest_cli_command,
        ["--exclude", "*.venv,__pycache__"],
    )

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(recorded_pipeline.only_run["exclude"]).is_equal_to(
        "*.venv,__pycache__",
    )


def test_test_command_include_venv(recorded_pipeline: PipelineRecorder) -> None:
    """``--include-venv`` turns the pipeline flag on.

    Args:
        recorded_pipeline: Recorder for the pipeline the command drives.
    """
    result = CliRunner().invoke(pytest_cli_command, ["--include-venv"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(recorded_pipeline.only_run["include_venv"]).is_true()


def test_test_command_output_format(recorded_pipeline: PipelineRecorder) -> None:
    """``--output-format`` selects the requested renderer.

    Args:
        recorded_pipeline: Recorder for the pipeline the command drives.
    """
    result = CliRunner().invoke(pytest_cli_command, ["--output-format", "json"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(recorded_pipeline.only_run["output_format"]).is_equal_to("json")


def test_test_command_group_by(recorded_pipeline: PipelineRecorder) -> None:
    """``--group-by`` selects the requested grouping.

    Args:
        recorded_pipeline: Recorder for the pipeline the command drives.
    """
    result = CliRunner().invoke(pytest_cli_command, ["--group-by", "code"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(recorded_pipeline.only_run["group_by"]).is_equal_to("code")


def test_test_command_rejects_group_by_category(
    recorded_pipeline: PipelineRecorder,
) -> None:
    """``lintro test`` does not advertise category grouping (pytest is raw).

    Args:
        recorded_pipeline: Recorder for the pipeline the command drives.
    """
    result = CliRunner().invoke(pytest_cli_command, ["--group-by", "category"])

    assert_that(result.exit_code).is_not_equal_to(0)
    assert_that(result.output).contains("Invalid value")
    assert_that(recorded_pipeline.runs).is_empty()


def test_test_command_verbose(recorded_pipeline: PipelineRecorder) -> None:
    """``--verbose`` turns the pipeline flag on.

    Args:
        recorded_pipeline: Recorder for the pipeline the command drives.
    """
    result = CliRunner().invoke(pytest_cli_command, ["--verbose"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(recorded_pipeline.only_run["verbose"]).is_true()


def test_test_command_raw_output(recorded_pipeline: PipelineRecorder) -> None:
    """``--raw-output`` turns the pipeline flag on.

    Args:
        recorded_pipeline: Recorder for the pipeline the command drives.
    """
    result = CliRunner().invoke(pytest_cli_command, ["--raw-output"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(recorded_pipeline.only_run["raw_output"]).is_true()


def test_test_command_list_plugins(recorded_pipeline: PipelineRecorder) -> None:
    """``--list-plugins`` becomes a prefixed pytest tool option.

    Args:
        recorded_pipeline: Recorder for the pipeline the command drives.
    """
    result = CliRunner().invoke(pytest_cli_command, ["--list-plugins"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(recorded_pipeline.only_run["tool_options"]).contains(
        "pytest:list_plugins=True",
    )


def test_test_command_check_plugins(recorded_pipeline: PipelineRecorder) -> None:
    """``--check-plugins`` is merged with the caller's own tool options.

    Args:
        recorded_pipeline: Recorder for the pipeline the command drives.
    """
    result = CliRunner().invoke(
        pytest_cli_command,
        [
            "--check-plugins",
            "--tool-options",
            "pytest:required_plugins=pytest-cov,pytest-xdist",
        ],
    )

    assert_that(result.exit_code).is_equal_to(0)
    tool_options = recorded_pipeline.only_run["tool_options"]
    assert_that(tool_options).contains("pytest:check_plugins=True")
    assert_that(tool_options).contains(
        "pytest:required_plugins=pytest-cov,pytest-xdist",
    )


@pytest.mark.parametrize(
    ("pipeline_exit_code", "expected_exit_code"),
    [(0, 0), (1, 1), (2, 2)],
    ids=["success", "failure", "error"],
)
def test_test_command_propagates_the_pipeline_exit_code(
    recorded_pipeline: PipelineRecorder,
    pipeline_exit_code: int,
    expected_exit_code: int,
) -> None:
    """The command exits with whatever code the pipeline returned.

    Args:
        recorded_pipeline: Recorder for the pipeline the command drives.
        pipeline_exit_code: Exit code the stand-in pipeline returns.
        expected_exit_code: Exit code the command is expected to produce.
    """
    recorded_pipeline.exit_code = pipeline_exit_code

    result = CliRunner().invoke(pytest_cli_command, [])

    assert_that(result.exit_code).is_equal_to(expected_exit_code)

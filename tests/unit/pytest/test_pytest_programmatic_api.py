"""Unit tests for pytest programmatic API."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.cli_utils.commands.test import test


def test_test_function_with_default_options() -> None:
    """Test programmatic test function with explicit default options."""
    with patch("lintro.api.core.run_lint_tools_simple", return_value=0) as mock_run:
        test(
            paths=(),
            exclude=None,
            include_venv=False,
            output=None,
            output_format="grid",
            group_by="file",
            verbose=False,
            tool_options=None,
        )
        assert_that(mock_run.called).is_true()
        assert_that(mock_run.call_args.kwargs["tools"]).is_equal_to("pytest")


def test_test_function_with_paths() -> None:
    """Test programmatic test function with paths."""
    with patch("lintro.api.core.run_lint_tools_simple", return_value=0) as mock_run:
        test(
            paths=("tests/",),
            exclude=None,
            include_venv=False,
            output=None,
            output_format="grid",
            group_by="file",
            verbose=False,
            tool_options=None,
        )
        assert_that(mock_run.call_args.kwargs["paths"]).contains("tests/")


def test_test_function_with_exclude() -> None:
    """Test programmatic test function with exclude patterns."""
    with patch("lintro.api.core.run_lint_tools_simple", return_value=0) as mock_run:
        test(
            paths=(),
            exclude="*.venv",
            include_venv=False,
            output=None,
            output_format="grid",
            group_by="file",
            verbose=False,
            tool_options=None,
        )
        assert_that(mock_run.call_args.kwargs["exclude"]).is_equal_to("*.venv")


def test_test_function_with_include_venv() -> None:
    """Test programmatic test function with include-venv."""
    with patch("lintro.api.core.run_lint_tools_simple", return_value=0) as mock_run:
        test(
            paths=(),
            exclude=None,
            include_venv=True,
            output=None,
            output_format="grid",
            group_by="file",
            verbose=False,
            tool_options=None,
        )
        assert_that(mock_run.call_args.kwargs["include_venv"]).is_true()


def test_test_function_with_output() -> None:
    """Test programmatic test function with output file."""
    with patch("lintro.api.core.run_lint_tools_simple", return_value=0) as mock_run:
        test(
            paths=(),
            exclude=None,
            include_venv=False,
            output="/tmp/output.txt",
            output_format="grid",
            group_by="file",
            verbose=False,
            tool_options=None,
        )
        assert_that(mock_run.call_args.kwargs["output_file"]).is_equal_to(
            "/tmp/output.txt",
        )


def test_test_function_with_output_format() -> None:
    """Test programmatic test function with output format."""
    with patch("lintro.api.core.run_lint_tools_simple", return_value=0) as mock_run:
        test(
            paths=(),
            exclude=None,
            include_venv=False,
            output=None,
            output_format="json",
            group_by="file",
            verbose=False,
            tool_options=None,
        )
        assert_that(mock_run.call_args.kwargs["output_format"]).is_equal_to("json")


def test_test_function_with_group_by() -> None:
    """Test programmatic test function with group-by."""
    with patch("lintro.api.core.run_lint_tools_simple", return_value=0) as mock_run:
        test(
            paths=(),
            exclude=None,
            include_venv=False,
            output=None,
            output_format="grid",
            group_by="code",
            verbose=False,
            tool_options=None,
        )
        assert_that(mock_run.call_args.kwargs["group_by"]).is_equal_to("code")


def test_test_function_with_verbose() -> None:
    """Test programmatic test function with verbose flag."""
    with patch("lintro.api.core.run_lint_tools_simple", return_value=0) as mock_run:
        test(
            paths=(),
            exclude=None,
            include_venv=False,
            output=None,
            output_format="grid",
            group_by="file",
            verbose=True,
            tool_options=None,
        )
        assert_that(mock_run.call_args.kwargs["verbose"]).is_true()


def test_test_function_with_raw_output() -> None:
    """Test programmatic test function with raw-output flag."""
    with patch("lintro.api.core.run_lint_tools_simple", return_value=0) as mock_run:
        test(
            paths=(),
            exclude=None,
            include_venv=False,
            output=None,
            output_format="grid",
            group_by="file",
            verbose=False,
            raw_output=True,
            tool_options=None,
        )
        assert_that(mock_run.call_args.kwargs["raw_output"]).is_true()


def test_test_function_with_tool_options() -> None:
    """Test programmatic test function with tool options."""
    with patch("lintro.api.core.run_lint_tools_simple", return_value=0) as mock_run:
        test(
            paths=(),
            exclude=None,
            include_venv=False,
            output=None,
            output_format="grid",
            group_by="file",
            verbose=False,
            tool_options="maxfail=5",
        )
        assert_that(mock_run.call_args.kwargs["tool_options"]).contains(
            "pytest:maxfail=5",
        )


def test_test_function_exit_code_success() -> None:
    """Test programmatic function returns on success code."""
    with patch("lintro.api.core.run_lint_tools_simple", return_value=0) as mock_run:
        # test() returns None on success, no assignment needed
        test(
            paths=(),
            exclude=None,
            include_venv=False,
            output=None,
            output_format="grid",
            group_by="file",
            verbose=False,
            tool_options=None,
        )
        assert_that(mock_run.called).is_true()


def test_test_function_exit_code_failure() -> None:
    """Test programmatic function exits with failure code."""
    with patch("lintro.api.core.run_lint_tools_simple", return_value=1):
        with pytest.raises(SystemExit) as exc_info:
            test(
                paths=(),
                exclude=None,
                include_venv=False,
                output=None,
                output_format="grid",
                group_by="file",
                verbose=False,
                tool_options=None,
            )
        assert_that(exc_info.value.code).is_equal_to(1)

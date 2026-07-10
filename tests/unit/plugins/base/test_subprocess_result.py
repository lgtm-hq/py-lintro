"""Unit tests for the SubprocessResult contract (issue #1043).

These tests verify that ``run_subprocess`` returns stdout and stderr as
separate streams so tool definitions can parse stdout only, without a stderr
warning line corrupting JSON parsing.
"""

from __future__ import annotations

import json
import sys

from assertpy import assert_that

from lintro.plugins.subprocess_executor import SubprocessResult, run_subprocess


def test_run_subprocess_returns_separate_streams() -> None:
    """run_subprocess keeps stdout and stderr as distinct fields."""
    script = "import sys; sys.stdout.write('OUT'); sys.stderr.write('ERR')"
    result = run_subprocess([sys.executable, "-c", script], timeout=30)

    assert_that(result).is_instance_of(SubprocessResult)
    assert_that(result.stdout).is_equal_to("OUT")
    assert_that(result.stderr).is_equal_to("ERR")
    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.success).is_true()


def test_run_subprocess_output_field_combines_streams() -> None:
    """The compatibility ``output`` field concatenates stdout and stderr."""
    script = "import sys; sys.stdout.write('OUT'); sys.stderr.write('ERR')"
    result = run_subprocess([sys.executable, "-c", script], timeout=30)

    assert_that(result.output).contains("OUT")
    assert_that(result.output).contains("ERR")


def test_run_subprocess_stdout_stays_clean_json_despite_stderr_noise() -> None:
    """A stderr warning does not corrupt JSON parsing of stdout.

    This is the core motivation for #1043: parsers can now consume stdout only.
    """
    script = (
        "import sys; "
        "sys.stderr.write('WARNING: deprecated flag\\n'); "
        "sys.stdout.write('{\"issues\": 1}')"
    )
    result = run_subprocess([sys.executable, "-c", script], timeout=30)

    parsed = json.loads(result.stdout)
    assert_that(parsed).is_equal_to({"issues": 1})
    assert_that(result.stderr).contains("WARNING")


def test_run_subprocess_reports_failure_returncode() -> None:
    """A non-zero exit is reflected in returncode and success."""
    result = run_subprocess(
        [sys.executable, "-c", "import sys; sys.exit(3)"],
        timeout=30,
    )

    assert_that(result.returncode).is_equal_to(3)
    assert_that(result.success).is_false()


def test_subprocess_result_as_tuple_matches_legacy_contract() -> None:
    """as_tuple() yields the legacy ``(success, output)`` pair."""
    result = SubprocessResult(returncode=0, stdout="a", stderr="b", output="ab")

    success, output = result.as_tuple()

    assert_that(success).is_true()
    assert_that(output).is_equal_to("ab")


def test_subprocess_result_as_tuple_reports_failure() -> None:
    """as_tuple() reports failure for a non-zero return code."""
    result = SubprocessResult(returncode=1, stdout="", stderr="boom", output="boom")

    success, output = result.as_tuple()

    assert_that(success).is_false()
    assert_that(output).is_equal_to("boom")

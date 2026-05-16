"""Integration tests for the coverage pipeline.

Tests the full pipeline from pytest output extraction through to PR comment
generation, verifying that data flows correctly between components.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

from assertpy import assert_that

# Compute repo root from this test file location (tests/scripts/test_*.py -> repo root)
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
EXTRACT_SCRIPT = (_REPO_ROOT / "scripts/ci/testing/extract-test-summary.sh").resolve()
COMMENT_SCRIPT = (_REPO_ROOT / "scripts/ci/github/coverage-pr-comment.sh").resolve()


def test_full_pipeline_extract_to_comment() -> None:
    """Test the full pipeline from pytest output to JSON summary.

    This integration test verifies that:
    1. extract-test-summary.sh correctly parses pytest output
    2. The generated JSON is valid and contains expected fields
    3. The JSON structure is compatible with coverage-pr-comment.sh parsing

    Note: We don't test coverage-pr-comment.sh execution directly since it
    requires GitHub Actions environment variables. Instead, we verify the
    JSON output format is correct for downstream consumption.
    """
    pytest_output = """
============================= test session starts ==============================
platform linux -- Python 3.13.1, pytest-8.0.0, pluggy-1.4.0
rootdir: /app
configfile: pyproject.toml
plugins: cov-5.0.0, anyio-4.0.0
collected 156 items

tests/unit/test_core.py .................................................. [ 32%]
tests/unit/test_utils.py .................................................. [ 64%]
tests/integration/test_api.py ............................................. [ 96%]
tests/integration/test_cli.py ......                                       [100%]

---------- coverage: platform linux, python 3.13.1 ----------
Name                      Stmts   Miss  Cover
---------------------------------------------
lintro/__init__.py            5      0   100%
lintro/core.py              150     15    90%
lintro/utils.py              80     10    88%
---------------------------------------------
TOTAL                       235     25    89%

============== 150 passed, 4 failed, 2 skipped in 45.67s ================
"""

    coverage_xml = """<?xml version="1.0" ?>
<coverage version="7.0.0" timestamp="1706472000" lines-covered="210" lines-valid="235" line-rate="0.8936" branch-rate="0" complexity="0">
    <packages>
        <package name="lintro" line-rate="0.8936" branch-rate="0" complexity="0">
            <classes>
                <class name="__init__.py" filename="lintro/__init__.py" line-rate="1.0" branch-rate="0" complexity="0">
                    <lines/>
                </class>
                <class name="core.py" filename="lintro/core.py" line-rate="0.9" branch-rate="0" complexity="0">
                    <lines/>
                </class>
                <class name="utils.py" filename="lintro/utils.py" line-rate="0.875" branch-rate="0" complexity="0">
                    <lines/>
                </class>
            </classes>
        </package>
    </packages>
</coverage>
"""

    with tempfile.TemporaryDirectory() as tmpdir:
        # Set up test files
        input_file = Path(tmpdir) / "test-output.log"
        output_file = Path(tmpdir) / "test-summary.json"
        coverage_file = Path(tmpdir) / "coverage.xml"

        input_file.write_text(pytest_output)
        coverage_file.write_text(coverage_xml)

        # Run extract-test-summary.sh
        result = subprocess.run(
            [str(EXTRACT_SCRIPT), str(input_file), str(output_file)],
            capture_output=True,
            text=True,
            cwd=tmpdir,
        )

        # Verify extraction succeeded
        assert_that(result.returncode).is_equal_to(0)
        assert_that(output_file.exists()).is_true()

        # Parse and validate JSON structure
        summary = json.loads(output_file.read_text())

        # Verify test summary fields
        assert_that(summary).contains_key("tests")
        tests = summary["tests"]
        assert_that(tests).contains_key("passed")
        assert_that(tests).contains_key("failed")
        assert_that(tests).contains_key("skipped")
        assert_that(tests).contains_key("errors")
        assert_that(tests).contains_key("total")
        assert_that(tests).contains_key("duration")

        # Verify test values
        assert_that(tests["passed"]).is_equal_to(150)
        assert_that(tests["failed"]).is_equal_to(4)
        assert_that(tests["skipped"]).is_equal_to(2)
        assert_that(tests["duration"]).is_equal_to(45.67)

        # Verify coverage fields
        assert_that(summary).contains_key("coverage")
        coverage = summary["coverage"]
        assert_that(coverage).contains_key("percentage")
        assert_that(coverage).contains_key("lines_covered")
        assert_that(coverage).contains_key("lines_total")
        assert_that(coverage).contains_key("lines_missing")
        assert_that(coverage).contains_key("files")

        # Verify coverage values from XML
        assert_that(coverage["lines_covered"]).is_equal_to(210)
        assert_that(coverage["lines_total"]).is_equal_to(235)
        assert_that(coverage["lines_missing"]).is_equal_to(25)
        assert_that(coverage["files"]).is_equal_to(3)

        # Verify JSON format is compatible with grep-based parsing
        # (single space after colon)
        raw_json = output_file.read_text()
        assert_that(raw_json).contains('"passed": ')
        assert_that(raw_json).contains('"failed": ')
        assert_that(raw_json).contains('"percentage": ')


def test_pipeline_handles_missing_coverage_xml() -> None:
    """Pipeline should work without coverage.xml, defaulting coverage to 0."""
    pytest_output = "10 passed in 1.00s\n"

    with tempfile.TemporaryDirectory() as tmpdir:
        input_file = Path(tmpdir) / "test-output.log"
        output_file = Path(tmpdir) / "test-summary.json"

        input_file.write_text(pytest_output)
        # Note: no coverage.xml created

        result = subprocess.run(
            [str(EXTRACT_SCRIPT), str(input_file), str(output_file)],
            capture_output=True,
            text=True,
            cwd=tmpdir,
        )

        assert_that(result.returncode).is_equal_to(0)

        summary = json.loads(output_file.read_text())
        assert_that(summary["tests"]["passed"]).is_equal_to(10)
        assert_that(summary["coverage"]["percentage"]).is_equal_to(0)
        assert_that(summary["coverage"]["files"]).is_equal_to(0)


def test_failed_test_comment_reports_test_execution_not_coverage() -> None:
    """Failed test runs should report test execution instead of coverage."""
    summary = {
        "tests": {
            "passed": 5192,
            "failed": 2,
            "skipped": 8,
            "errors": 0,
            "total": 5202,
            "duration": 125.84,
        },
        "coverage": {
            "percentage": 0,
            "lines_covered": 5458,
            "lines_total": 19750,
            "lines_missing": 14292,
            "files": 393,
        },
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        workdir = Path(tmpdir)
        (workdir / "test-summary.json").write_text(json.dumps(summary))

        env = {
            **os.environ,
            "COVERAGE_PERCENTAGE": "0.0",
            "GITHUB_EVENT_NAME": "pull_request",
            "GITHUB_REPOSITORY": "lgtm-hq/py-lintro",
            "GITHUB_RUN_ID": "123456",
            "GITHUB_SHA": "abc123",
            "JOB_RESULT": "failure",
        }
        result = subprocess.run(
            [str(COMMENT_SCRIPT)],
            capture_output=True,
            text=True,
            cwd=workdir,
            env=env,
        )

        assert_that(result.returncode).is_equal_to(0)
        comment = (workdir / "coverage-pr-comment.txt").read_text()
        assert_that(comment).contains("## 🧪 Test Execution Report")
        assert_that(comment).contains("### 📊 Status: Tests failed")
        assert_that(comment).contains(
            "| 🧪 pytest | ❌ FAIL | 5192 | 2 | 0 | 8 | 5202 | 125.84s |",
        )
        assert_that(comment).does_not_contain("**Coverage:**")
        assert_that(comment).does_not_contain("HTML Coverage Report")


def test_cancelled_test_comment_reports_interrupted_execution() -> None:
    """Cancelled test runs should post an explicit interrupted test report."""
    with tempfile.TemporaryDirectory() as tmpdir:
        workdir = Path(tmpdir)

        env = {
            **os.environ,
            "COVERAGE_PERCENTAGE": "0.0",
            "GITHUB_EVENT_NAME": "pull_request",
            "GITHUB_REPOSITORY": "lgtm-hq/py-lintro",
            "GITHUB_RUN_ID": "123456",
            "GITHUB_SHA": "abc123",
            "JOB_RESULT": "cancelled",
        }
        result = subprocess.run(
            [str(COMMENT_SCRIPT)],
            capture_output=True,
            text=True,
            cwd=workdir,
            env=env,
        )

        assert_that(result.returncode).is_equal_to(0)
        comment = (workdir / "coverage-pr-comment.txt").read_text()
        assert_that(comment).contains("## 🧪 Test Execution Report")
        assert_that(comment).contains("### 📊 Status: Tests cancelled")
        assert_that(comment).contains("Tests cancelled before completion")
        assert_that(comment).does_not_contain("**Coverage:**")
        assert_that(comment).does_not_contain("HTML Coverage Report")


def test_pipeline_quiet_mode_produces_valid_json() -> None:
    """Pipeline in quiet mode should still produce valid JSON output."""
    pytest_output = "5 passed, 1 failed in 2.50s\n"

    with tempfile.TemporaryDirectory() as tmpdir:
        input_file = Path(tmpdir) / "test-output.log"
        output_file = Path(tmpdir) / "test-summary.json"

        input_file.write_text(pytest_output)

        result = subprocess.run(
            [str(EXTRACT_SCRIPT), "--quiet", str(input_file), str(output_file)],
            capture_output=True,
            text=True,
            cwd=tmpdir,
        )

        assert_that(result.returncode).is_equal_to(0)
        # Quiet mode should suppress stdout
        assert_that(result.stdout).is_empty()

        # But JSON should still be valid
        summary = json.loads(output_file.read_text())
        assert_that(summary["tests"]["passed"]).is_equal_to(5)
        assert_that(summary["tests"]["failed"]).is_equal_to(1)

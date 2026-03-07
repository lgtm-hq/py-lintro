"""Tests for extract-test-summary.sh script.

This module tests the extract-test-summary.sh script which parses pytest output
and extracts test results into a JSON summary file for PR comments.
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
SCRIPT_PATH = _REPO_ROOT / "scripts/ci/testing/extract-test-summary.sh"


def test_script_help_output() -> None:
    """Script should display help and exit 0 with --help flag."""
    result = subprocess.run(
        [str(SCRIPT_PATH), "--help"],
        capture_output=True,
        text=True,
    )

    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("Usage:")
    assert_that(result.stdout).contains("extract-test-summary.sh")
    assert_that(result.stdout).contains("test-output-file")
    assert_that(result.stdout).contains("output-json-file")


def test_script_syntax_check() -> None:
    """Script should pass bash syntax check."""
    result = subprocess.run(
        ["bash", "-n", str(SCRIPT_PATH)],
        capture_output=True,
        text=True,
    )

    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stderr).is_empty()


def test_extract_from_standard_pytest_output() -> None:
    """Extract test summary from standard pytest output format."""
    pytest_output = """
============================= test session starts ==============================
platform linux -- Python 3.13.1, pytest-8.0.0, pluggy-1.4.0
collected 100 items

tests/test_foo.py ............................                           [ 28%]
tests/test_bar.py ............................                           [ 56%]
tests/test_baz.py ............................                           [ 84%]
tests/test_qux.py ................                                       [100%]

=============================== warnings summary ===============================
1 warning

============== 95 passed, 3 failed, 2 skipped in 12.34s ================
"""
    with tempfile.TemporaryDirectory() as tmpdir:
        input_file = Path(tmpdir) / "test-output.log"
        output_file = Path(tmpdir) / "test-summary.json"
        input_file.write_text(pytest_output)

        result = subprocess.run(
            [str(SCRIPT_PATH), str(input_file), str(output_file)],
            capture_output=True,
            text=True,
            cwd=tmpdir,
        )

        assert_that(result.returncode).is_equal_to(0)
        assert_that(output_file.exists()).is_true()

        summary = json.loads(output_file.read_text())
        assert_that(summary["tests"]["passed"]).is_equal_to(95)
        assert_that(summary["tests"]["failed"]).is_equal_to(3)
        assert_that(summary["tests"]["skipped"]).is_equal_to(2)
        assert_that(summary["tests"]["duration"]).is_equal_to(12.34)


def test_extract_from_lintro_table_format() -> None:
    """Extract test summary from lintro table format output.

    Note: The pattern supports both emoji and non-emoji formats
    (e.g., '| 🧪 pytest' or '| pytest').
    """
    lintro_output = """
| Tool | Status | Passed | Failed | Skipped | Total | Duration |
|------|--------|--------|--------|---------|-------|----------|
| pytest | PASS | 150 | 0 | 5 | 155 | 45.67s |
"""
    with tempfile.TemporaryDirectory() as tmpdir:
        input_file = Path(tmpdir) / "test-output.log"
        output_file = Path(tmpdir) / "test-summary.json"
        input_file.write_text(lintro_output)

        result = subprocess.run(
            [str(SCRIPT_PATH), str(input_file), str(output_file)],
            capture_output=True,
            text=True,
            cwd=tmpdir,
        )

        assert_that(result.returncode).is_equal_to(0)
        assert_that(output_file.exists()).is_true()

        summary = json.loads(output_file.read_text())
        assert_that(summary["tests"]["passed"]).is_equal_to(150)
        assert_that(summary["tests"]["failed"]).is_equal_to(0)
        assert_that(summary["tests"]["skipped"]).is_equal_to(5)
        assert_that(summary["tests"]["total"]).is_equal_to(155)


def test_extract_with_coverage_xml() -> None:
    """Extract both test summary and coverage data when coverage.xml exists."""
    pytest_output = "10 passed in 5.00s\n"
    coverage_xml = """<?xml version="1.0" ?>
<coverage version="7.0.0" timestamp="1234567890" lines-covered="800" \
           lines-valid="1000" line-rate="0.8" branch-rate="0.0" complexity="0">
    <packages>
        <package name="lintro" line-rate="0.8" branch-rate="0.0" complexity="0">
            <classes>
                <class name="foo.py" filename="lintro/foo.py"
                      line-rate="0.9" branch-rate="0.0" complexity="0">
                    <lines/>
                </class>
                <class name="bar.py" filename="lintro/bar.py"
                      line-rate="0.7" branch-rate="0.0" complexity="0">
                    <lines/>
                </class>
            </classes>
        </package>
    </packages>
</coverage>
"""
    with tempfile.TemporaryDirectory() as tmpdir:
        input_file = Path(tmpdir) / "test-output.log"
        output_file = Path(tmpdir) / "test-summary.json"
        coverage_file = Path(tmpdir) / "coverage.xml"

        input_file.write_text(pytest_output)
        coverage_file.write_text(coverage_xml)

        result = subprocess.run(
            [str(SCRIPT_PATH), str(input_file), str(output_file)],
            capture_output=True,
            text=True,
            cwd=tmpdir,
        )

        assert_that(result.returncode).is_equal_to(0)

        summary = json.loads(output_file.read_text())
        assert_that(summary["coverage"]["percentage"]).is_equal_to(80.0)
        assert_that(summary["coverage"]["lines_covered"]).is_equal_to(800)
        assert_that(summary["coverage"]["lines_total"]).is_equal_to(1000)
        assert_that(summary["coverage"]["lines_missing"]).is_equal_to(200)
        assert_that(summary["coverage"]["files"]).is_equal_to(2)


def test_extract_from_environment_variables() -> None:
    """Extract test summary from environment variables when no input file."""
    env = os.environ.copy()
    env["TEST_PASSED"] = "42"
    env["TEST_FAILED"] = "1"
    env["TEST_SKIPPED"] = "3"
    env["TEST_ERRORS"] = "0"
    env["TEST_TOTAL"] = "46"
    env["TEST_DURATION"] = "8.5"

    with tempfile.TemporaryDirectory() as tmpdir:
        output_file = Path(tmpdir) / "test-summary.json"

        result = subprocess.run(
            [str(SCRIPT_PATH), "", str(output_file)],
            capture_output=True,
            text=True,
            cwd=tmpdir,
            env=env,
        )

        assert_that(result.returncode).is_equal_to(0)

        summary = json.loads(output_file.read_text())
        assert_that(summary["tests"]["passed"]).is_equal_to(42)
        assert_that(summary["tests"]["failed"]).is_equal_to(1)
        assert_that(summary["tests"]["skipped"]).is_equal_to(3)
        assert_that(summary["tests"]["total"]).is_equal_to(46)


def test_default_output_file() -> None:
    """Script uses test-summary.json as default output file."""
    pytest_output = "5 passed in 1.00s\n"

    with tempfile.TemporaryDirectory() as tmpdir:
        input_file = Path(tmpdir) / "test-output.log"
        input_file.write_text(pytest_output)

        # Run without specifying output file
        result = subprocess.run(
            [str(SCRIPT_PATH), str(input_file)],
            capture_output=True,
            text=True,
            cwd=tmpdir,
        )

        assert_that(result.returncode).is_equal_to(0)

        # Default output file should be created
        default_output = Path(tmpdir) / "test-summary.json"
        assert_that(default_output.exists()).is_true()

        summary = json.loads(default_output.read_text())
        assert_that(summary["tests"]["passed"]).is_equal_to(5)

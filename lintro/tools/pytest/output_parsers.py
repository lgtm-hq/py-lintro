"""Output parsing with format detection and fallback for pytest.

This module provides output parsing with automatic format detection.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger

from lintro.parsers.pytest.pytest_issue import PytestIssue
from lintro.parsers.pytest.pytest_parser import parse_pytest_output


def parse_pytest_output_with_fallback(
    output: str,
    return_code: int,
    options: dict[str, Any],
    subprocess_start_time: float | None = None,
) -> list[PytestIssue]:
    """Parse pytest output into issues with format detection and fallback.

    Prioritizes JSON format when available, then JUnit XML, then falls back to text.
    Validates parsed output structure to ensure reliability.
    Always tries to parse JUnit XML file if available to capture skipped tests.

    Args:
        output: Raw output from pytest.
        return_code: Return code from pytest.
        options: Options dictionary.
        subprocess_start_time: Optional Unix timestamp when subprocess started.
            If provided, only JUnit XML files modified after this time will be read.

    Returns:
        list[PytestIssue]: Parsed test failures, errors, and skips.
    """
    issues: list[PytestIssue] = []

    # Try to parse JUnit XML file if it exists and was explicitly requested
    # This captures all test results including skips when using JUnit XML format
    # But only if the output we're parsing is not already JUnit XML
    # AND we're not in JSON mode (prioritize JSON over JUnit XML)
    # Check this BEFORE early return to ensure JUnit XML parsing happens even
    # when output is empty (e.g., quiet mode or redirected output)
    junitxml_path = None
    if (
        options.get("junitxml")
        and (not output or not output.strip().startswith("<?xml"))
        and not options.get("json_report", False)
    ):
        junitxml_path = options.get("junitxml")

    # Early return only if output is empty AND no JUnit XML file to parse
    if not output and not (junitxml_path and Path(junitxml_path).exists()):
        return []

    if junitxml_path and Path(junitxml_path).exists():
        # Only read the file if it was modified after subprocess started
        # This prevents reading stale files from previous test runs
        junitxml_file = Path(junitxml_path)
        file_mtime = junitxml_file.stat().st_mtime
        should_read = True

        if subprocess_start_time is not None and file_mtime < subprocess_start_time:
            logger.debug(
                f"Skipping stale JUnit XML file {junitxml_path} "
                f"(modified before subprocess started)",
            )
            should_read = False

        if should_read:
            try:
                with open(junitxml_path, encoding="utf-8") as f:
                    junit_content = f.read()
                junit_issues = parse_pytest_output(junit_content, format="junit")
                if junit_issues:
                    issues.extend(junit_issues)
                    logger.debug(
                        f"Parsed {len(junit_issues)} issues from JUnit XML file",
                    )
            except OSError as e:
                logger.debug(f"Failed to read JUnit XML file {junitxml_path}: {e}")

    # If we already have issues from JUnit XML, return them
    # Otherwise, fall back to parsing the output
    if issues:
        return issues

    # Try to detect output format automatically
    # Priority: JSON > JUnit XML > Text
    output_format = "text"

    # Check for JSON format (pytest-json-report)
    if options.get("json_report", False):
        output_format = "json"
    elif options.get("junitxml"):
        output_format = "junit"
    else:
        # Auto-detect format from output content
        # Check for JSON report file reference or JSON content
        if "pytest-report.json" in output or (
            output.strip().startswith("{") and "test_reports" in output
        ):
            output_format = "json"
        # Check for JUnit XML structure
        elif output.strip().startswith("<?xml") and "<testsuite" in output:
            output_format = "junit"
        # Default to text parsing
        else:
            output_format = "text"

    # Parse based on detected format
    issues = parse_pytest_output(output, format=output_format)

    # Validate parsed output structure
    if not isinstance(issues, list):
        logger.warning(
            f"Parser returned unexpected type: {type(issues)}, "
            "falling back to text parsing",
        )
        issues = []
    else:
        # Validate that all items are PytestIssue instances
        validated_issues = []
        for issue in issues:
            if isinstance(issue, PytestIssue):
                validated_issues.append(issue)
            else:
                logger.warning(
                    f"Skipping invalid issue type: {type(issue)}",
                )
        issues = validated_issues

    # If no issues found but return code indicates failure, try text parsing
    if not issues and return_code != 0 and output_format != "text":
        logger.debug(
            f"No issues parsed from {output_format} format, "
            "trying text parsing fallback",
        )
        fallback_issues = parse_pytest_output(output, format="text")
        if fallback_issues:
            logger.info(
                f"Fallback text parsing found {len(fallback_issues)} issues",
            )
            issues = fallback_issues

    return issues

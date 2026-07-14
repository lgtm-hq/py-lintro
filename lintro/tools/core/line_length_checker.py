"""Shared utility for checking line length violations.

This module provides a decoupled way to check for E501 (line too long) violations
using Ruff as the underlying checker. It avoids direct tool-to-tool imports,
making the architecture more modular.
"""

from __future__ import annotations

import json
import shutil
import subprocess  # nosec B404 - used safely with shell disabled
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from lintro.utils.path_utils import absolute_path


@dataclass
class LineLengthViolation:
    """Represents a line length violation.

    This is a tool-agnostic data class that can be converted to any
    tool-specific issue format (e.g., BlackIssue, RuffIssue).

    Attributes:
        file: Absolute path to the file with the violation.
        line: Line number where the violation occurs.
        column: Column number (typically where the line exceeds the limit).
        message: Description of the violation from Ruff.
        code: The rule code (E501).
    """

    file: str
    line: int
    column: int
    message: str
    code: str = "E501"


def check_line_length_violations(
    files: list[str],
    cwd: str | None = None,
    line_length: int | None = None,
    timeout: int = 30,
) -> list[LineLengthViolation]:
    """Check files for line length violations using Ruff's E501 rule.

    This function runs Ruff via subprocess to detect lines that exceed
    the configured line length limit. It's designed to be used by formatters
    like Black that cannot wrap certain long lines.

    Args:
        files: List of file paths to check. Can be relative (to cwd) or absolute.
        cwd: Working directory for relative paths. If None, paths are treated
            as relative to the current directory.
        line_length: Maximum line length. If None, uses Ruff's default (88).
        timeout: Timeout in seconds for the Ruff subprocess.

    Returns:
        List of LineLengthViolation objects representing E501 violations.
        Returns an empty list if Ruff is not available or if an error occurs.

    Example:
        >>> violations = check_line_length_violations(
        ...     files=["src/module.py"],
        ...     cwd="/project",
        ...     line_length=100,
        ... )
        >>> for v in violations:
        ...     print(f"{v.file}:{v.line} - {v.message}")
    """
    if not files:
        return []

    # Check if Ruff is available
    ruff_path = shutil.which("ruff")
    if not ruff_path:
        logger.debug("Ruff not found in PATH, skipping line length check")
        return []

    abs_files = [
        absolute_path(file_path, base_dir=Path(cwd) if cwd else None)
        for file_path in files
    ]

    # Build the Ruff command
    cmd: list[str] = [
        ruff_path,
        "check",
        "--select",
        "E501",
        "--output-format",
        "json",
        "--no-cache",  # Avoid cache issues when checking specific files
    ]

    if line_length is not None:
        cmd.extend(["--line-length", str(line_length)])

    cmd.extend(abs_files)

    logger.debug(f"Running line length check: {' '.join(cmd)}")

    try:
        result = subprocess.run(  # nosec B603 - argv is an internally-built list run with shell=False; binary resolved from a known command, no user shell input
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            check=False,  # Don't raise on non-zero exit (violations cause exit 1)
        )

        # Parse JSON output
        if not result.stdout.strip():
            return []

        try:
            issues_data = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            logger.debug(f"Failed to parse Ruff JSON output: {e}")
            return []

        # Convert to LineLengthViolation objects
        violations: list[LineLengthViolation] = []
        for issue in issues_data:
            # Ruff JSON format has: filename, row, column, message, code
            file_path = issue.get("filename", "")
            if not Path(file_path).is_absolute() and cwd:
                file_path = absolute_path(file_path, base_dir=Path(cwd))

            # Handle both old and new Ruff JSON formats
            line = issue.get("location", {}).get("row") or issue.get("row", 0)
            column = issue.get("location", {}).get("column") or issue.get("column", 0)

            violations.append(
                LineLengthViolation(
                    file=file_path,
                    line=line,
                    column=column,
                    message=issue.get("message", "Line too long"),
                    code=issue.get("code", "E501"),
                ),
            )

        return violations

    except subprocess.TimeoutExpired:
        logger.debug(f"Line length check timed out after {timeout}s")
        return []
    except FileNotFoundError:
        logger.debug("Ruff executable not found")
        return []
    except (OSError, ValueError, RuntimeError) as e:
        logger.debug(f"Failed to check line length violations: {e}")
        return []

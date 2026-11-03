"""Parser for Clippy cargo diagnostic JSON output."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from lintro.parsers.base_parser import strip_ansi_codes
from lintro.parsers.clippy.clippy_issue import ClippyIssue


@dataclass
class ClippyDiagnostics:
    """Independent diagnostic measurements, including rustc lint verdicts."""

    issues: list[ClippyIssue] = field(default_factory=list)
    failures: int = 0
    compiler_errors: int = 0
    lint_diagnostics: int = 0


def _diagnostic_code(value: object) -> str:
    """Read Rustc's nullable code object, retaining legacy string support.

    Args:
        value: Diagnostic code object or null.

    Returns:
        Diagnostic identifier, or an empty string for an uncoded message.

    Raises:
        ValueError: If a non-null code cannot identify a diagnostic.
    """
    if value is None:
        return ""
    code = value.get("code") if isinstance(value, dict) else value
    if not isinstance(code, str) or not code.strip():
        raise ValueError("invalid diagnostic code")
    return code


def _diagnostic_span(spans: list[Any]) -> dict[str, Any]:
    """Read the first location without rejecting valid global diagnostics.

    Args:
        spans: Rustc diagnostic locations, which may be empty.

    Returns:
        First span or an explicit unknown location.

    Raises:
        ValueError: If the first span cannot supply a filename.
    """
    if not spans:
        return {"file_name": "<unknown>"}
    span = spans[0]
    if not isinstance(span, dict):
        raise ValueError("invalid diagnostic span")
    file_name = span.get("file_name")
    if not isinstance(file_name, str) or not file_name.strip():
        raise ValueError("invalid diagnostic filename")
    return span


def _parse_record(item: dict[str, Any]) -> tuple[ClippyIssue | None, int, int, int]:
    """Separate usable Clippy findings, corrupt diagnostics and compiler errors.

    Args:
        item: One decoded Cargo JSON record.

    Returns:
        Optional finding, malformed, compiler-error and lint-diagnostic counts.
    """
    if item.get("reason") != "compiler-message":
        return None, 0, 0, 0
    try:
        message = item.get("message")
        if not isinstance(message, dict):
            return None, 1, 0, 0
        code = _diagnostic_code(message.get("code"))
        text = message.get("message")
        level = message.get("level")
        spans = message.get("spans")
        if (
            not isinstance(text, str)
            or not text.strip()
            or not isinstance(level, str)
            or not level.strip()
            or not isinstance(spans, list)
        ):
            return None, 1, 0, 0
        span = _diagnostic_span(spans)
        line_start = span.get("line_start")
        column_start = span.get("column_start")
        line_end = span.get("line_end")
        column_end = span.get("column_end")
        line = int(line_start) if line_start is not None else 0
        column = int(column_start) if column_start is not None else 0
        end_line = int(line_end) if line_end is not None else line
        end_column = int(column_end) if column_end is not None else column
        if not code.startswith("clippy::"):
            # Rustc lint names (including denied lints) differ from hard E####
            # errors and uncoded errors such as compile_error! diagnostics.
            lint = level in {"warning", "error"} and bool(
                re.fullmatch(r"[a-z][a-z0-9_]*", code),
            )
            return None, 0, int(level.startswith("error") and not lint), int(lint)
        return (
            ClippyIssue(
                file=span["file_name"],
                line=line,
                column=column,
                code=code,
                message=text.strip(),
                level=level,
                end_line=end_line if end_line != line else None,
                end_column=end_column if end_column != column else None,
            ),
            0,
            0,
            1,
        )
    except (TypeError, ValueError, OverflowError) as exc:
        logger.debug(f"Failed to parse clippy diagnostic: {exc}")
        return None, 1, 0, 0


def parse_clippy_diagnostics(output: str) -> ClippyDiagnostics:
    """Parse Cargo JSON Lines and account for discarded malformed records.

    Cargo permits arbitrary non-JSON progress output, so only lines starting
    with an object opener are decoded. Artifacts, build completion and future
    non-diagnostic reasons are ignored. Rustc permits nullable diagnostic codes,
    empty spans and future severity strings. Compiler errors are counted
    separately so a fix cannot hide them behind usable Clippy findings.

    Args:
        output: Raw output emitted by cargo clippy using --message-format=json.

    Returns:
        Clippy findings, malformed records, hard errors and lint verdicts.
        Empty output and valid non-Clippy diagnostics have no parse failures.
    """
    result = ClippyDiagnostics()
    for line in strip_ansi_codes(output).splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            logger.debug(f"Failed to decode clippy JSON line: {exc}")
            result.failures += 1
            continue
        issue, malformed, errors, lints = _parse_record(data)
        result.failures += malformed
        result.compiler_errors += errors
        result.lint_diagnostics += lints
        if issue is not None:
            result.issues.append(issue)
    return result


def parse_clippy_output_with_stats(output: str) -> tuple[list[ClippyIssue], int, int]:
    """Return findings and failure counts through the existing statistics API.

    Args:
        output: Raw Cargo JSON Lines and progress output.

    Returns:
        Usable Clippy findings, malformed-record count and compiler-error count.
    """
    result = parse_clippy_diagnostics(output)
    return result.issues, result.failures, result.compiler_errors


def parse_clippy_output(output: str) -> list[ClippyIssue]:
    """Return Clippy findings through the compatible list-only parser API.

    Args:
        output: Raw output emitted by cargo clippy using --message-format=json.

    Returns:
        Usable Clippy findings, omitting parser and native-error statistics.
    """
    issues, _failures, _compiler_errors = parse_clippy_output_with_stats(output)
    return issues

"""Parser for Stylelint JSON output.

Handles the ``--formatter json`` output produced by stylelint. The formatter
emits a JSON array with one object per linted source file, each carrying a
``warnings`` list (rule violations and ``CssSyntaxError`` parse failures) plus
``parseErrors`` and ``invalidOptionWarnings`` metadata arrays.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from loguru import logger

from lintro.parsers.stylelint.stylelint_issue import StylelintIssue


def _looks_like_stylelint_payload(data: Any) -> bool:
    """Return True when ``data`` matches Stylelint's per-file result array.

    Args:
        data: A decoded JSON value.

    Returns:
        True if ``data`` contains Stylelint file results. Invalid siblings are
        left for the parser to count, preserving usable findings.
    """
    if not isinstance(data, list):
        return False
    stylelint_keys = {"source", "warnings", "parseErrors", "invalidOptionWarnings"}
    return any(
        isinstance(item, dict) and stylelint_keys.intersection(item) for item in data
    )


def _outer_json_spans(output: str) -> Iterator[tuple[int, int | None]]:
    """Locate balanced outer values in one pass, respecting JSON strings.

    Args:
        output: Mixed formatter output and log text.

    Yields:
        tuple[int, int | None]: Start and end offsets of outer arrays or
        objects. An unfinished trailing container has no end offset.
    """
    depth = 0
    start = 0
    in_string = False
    escaped = False
    for index, char in enumerate(output):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"' and depth:
            in_string = True
        elif char in "[{":
            if not depth:
                start = index
            depth += 1
        elif char in "]}" and depth:
            depth -= 1
            if not depth:
                yield start, index + 1
    if depth:
        yield start, None


def _extract_json_array(output: str) -> str | None:
    """Extract the Stylelint JSON array substring from mixed tool output.

    Stylelint writes its JSON payload to stderr, which lintro combines with
    stdout. Skip non-Stylelint JSON arrays (e.g. ``["warning"]`` noise) and
    bracketed log lines so the real payload is selected. Only decode balanced
    outer values, avoiding repeated scans of nested or truncated JSON.

    Args:
        output: The combined stdout/stderr from stylelint.

    Returns:
        The JSON array substring, or None if no Stylelint array is found.
    """
    decoder = json.JSONDecoder()
    # Find standalone [] lines once, rather than rescanning a long log line
    # for every empty array it contains.
    empty_starts = set()
    offset = 0
    for line in output.splitlines(keepends=True):
        if line.strip() == "[]":
            empty_starts.add(offset + line.index("["))
        offset += len(line)
    empty_report = None
    for start, end in _outer_json_spans(output):
        if end is None:
            return None
        try:
            data = decoder.decode(output[start:end])
        except (json.JSONDecodeError, ValueError):
            continue
        if _looks_like_stylelint_payload(data):
            return output[start:end]
        if data == [] and start in empty_starts:
            # An empty report must occupy its own line. Logging like
            # "Plugins: []" is not a report, and must not hide a later one.
            empty_report = output[start:end]
    return empty_report


def parse_stylelint_output(output: str | None) -> list[StylelintIssue]:
    """Return Stylelint findings through the legacy list API.

    Args:
        output: Raw JSON formatter output, possibly surrounded by log text.

    Returns:
        All usable findings, omitting parser failure accounting.
    """
    issues, _failures = parse_stylelint_output_with_stats(output)
    return issues


def parse_stylelint_output_with_stats(
    output: str | None,
) -> tuple[list[StylelintIssue], int]:
    """Parse Stylelint JSON output into a list of StylelintIssue objects.

    Args:
        output: The raw combined output from stylelint ``--formatter json``.

    Returns:
        Usable findings and the count of invalid report containers or records.
    """
    issues: list[StylelintIssue] = []

    if not output or not output.strip():
        return issues, 0

    try:
        data: Any = json.loads(output)
    except json.JSONDecodeError:
        json_content = _extract_json_array(output)
        if json_content is None:
            return issues, 1
        data = json.loads(json_content)

    if not isinstance(data, list):
        logger.debug("Stylelint output is not a JSON array")
        return issues, 1

    failures = 0
    for source_result in data:
        if not isinstance(source_result, dict):
            failures += 1
            continue
        source = str(source_result.get("source", "") or "")
        source_issues, source_failures = _parse_source_result(source_result, source)
        issues.extend(source_issues)
        failures += source_failures

    return issues, failures


def _parse_source_result(
    source_result: dict[str, Any],
    source: str,
) -> tuple[list[StylelintIssue], int]:
    """Parse a single per-file result object into issues.

    Args:
        source_result: One entry from the stylelint JSON array.
        source: Resolved source file path for the entry.

    Returns:
        Usable issues and the count of malformed containers or records.
    """
    issues: list[StylelintIssue] = []
    failures = 0
    for key, default_rule in (
        ("warnings", ""),
        ("parseErrors", "parseError"),
        ("invalidOptionWarnings", "invalidOption"),
    ):
        # warnings is required; the two metadata arrays are optional.
        records = source_result.get(key, None if key == "warnings" else [])
        if not isinstance(records, list):
            failures += 1
            continue
        for record in records:
            issue = _parse_warning(record, source, default_rule)
            if issue is None:
                failures += 1
            else:
                issues.append(issue)
    return issues, failures


def _parse_warning(
    warning: Any,
    source: str,
    default_rule: str = "",
) -> StylelintIssue | None:
    """Parse a single stylelint warning object into a StylelintIssue.

    Args:
        warning: A single warning/parseError dictionary from stylelint output.
        source: Source file path the warning belongs to.
        default_rule: Fallback rule code when the entry has no ``rule`` key.

    Returns:
        StylelintIssue if parsing succeeds, None otherwise.
    """
    if not isinstance(warning, dict):
        return None

    text = str(warning.get("text", "") or "")
    if not text:
        return None

    rule = str(warning.get("rule", "") or default_rule)
    severity = str(warning.get("severity", "error") or "error")

    line_raw = warning.get("line", 0)
    column_raw = warning.get("column", 0)
    try:
        line = int(line_raw) if line_raw is not None else 0
    except (TypeError, ValueError):
        line = 0
    try:
        column = int(column_raw) if column_raw is not None else 0
    except (TypeError, ValueError):
        column = 0

    return StylelintIssue(
        file=source,
        line=line,
        column=column,
        code=rule,
        message=text,
        severity=severity,
        fixable=False,
    )

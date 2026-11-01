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


def _starts_json_container(output: str, index: int) -> bool:
    """Distinguish JSON openers from unmatched delimiters in log text.

    Args:
        output: Mixed formatter output and log text.
        index: Position of an opening array or object delimiter.

    Returns:
        Whether the next token can begin JSON. A bare opener on its own line
        remains a container, so truncated and nested reports fail closed.
    """
    next_index = index + 1
    while next_index < len(output) and output[next_index] in " \t\r":
        next_index += 1
    if next_index == len(output) or output[next_index] == "\n":
        line_start = output.rfind("\n", 0, index) + 1
        return not output[line_start:index].strip()
    first = output[next_index]
    if output[index] == "{":
        return first in '"}'
    return first in '[{]"0123456789-' or any(
        output.startswith(literal, next_index) for literal in ("true", "false", "null")
    )


def _standalone_scalar_spans(output: str) -> dict[int, int]:
    """Locate JSON scalar lines without repeatedly decoding overlapping text.

    Args:
        output: Mixed formatter output and log text.

    Returns:
        Start and end offsets of scalar lines, including any inside containers.
    """
    decoder = json.JSONDecoder()
    spans = {}
    offset = 0
    for line in output.splitlines(keepends=True):
        value = line.strip()
        if value and value[0] not in "[{":
            try:
                decoder.decode(value)
            except (json.JSONDecodeError, ValueError):
                pass
            else:
                start = offset + len(line) - len(line.lstrip())
                spans[start] = offset + len(line)
        offset += len(line)
    return spans


def _outer_json_spans(output: str) -> Iterator[tuple[int, int | None]]:
    """Locate outer containers and standalone scalars in bounded linear passes.

    Args:
        output: Mixed formatter output and log text.

    Yields:
        tuple[int, int | None]: Start and end offsets of outer JSON values.
        An unfinished trailing container has no end offset.
    """
    scalar_spans = _standalone_scalar_spans(output)
    depth = 0
    start = 0
    in_string = False
    escaped = False
    index = 0
    while index < len(output):
        char = output[index]
        if not depth and index in scalar_spans:
            end = scalar_spans[index]
            yield index, end
            # Delimiters inside a scalar string cannot start another report.
            index = end
            continue
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"' and depth:
            in_string = True
        elif char in "[{" and (depth or _starts_json_container(output, index)):
            if not depth:
                start = index
            depth += 1
        elif char in "]}" and depth:
            depth -= 1
            if not depth:
                yield start, index + 1
        index += 1
    if depth:
        yield start, None


def _extract_json_array(output: str) -> tuple[list[Any] | None, int]:
    """Extract usable Stylelint records and count invalid outer siblings.

    Stylelint writes its JSON payload to stderr, which lintro combines with
    stdout. Skip non-Stylelint JSON arrays (e.g. ``["warning"]`` noise) and
    bracketed log lines so the real payload is selected. Only decode balanced
    outer containers and standalone scalar lines, avoiding repeated scans of
    nested or truncated JSON. Scalar reports count as invalid outer siblings.

    Args:
        output: The combined stdout/stderr from stylelint.

    Returns:
        Decoded source records (or None without a report) and extraction
        failures. All outer spans are scanned, including after usable reports.
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
    reports: list[Any] = []
    empty_report = False
    failures = 0
    log_arrays = False
    for start, end in _outer_json_spans(output):
        if end is None:
            failures += 1
            break
        try:
            data = decoder.decode(output[start:end])
        except (json.JSONDecodeError, ValueError):
            failures += 1
            continue
        if _looks_like_stylelint_payload(data):
            reports.extend(data)
        elif data == [] and start in empty_starts:
            # An empty report must occupy its own line. Logging like
            # "Plugins: []" is not a report, and must not hide a later one.
            empty_report = True
        elif (
            isinstance(data, list)
            and data
            and all(
                isinstance(item, str)
                or (isinstance(item, dict) and item.get("type") == "warning")
                for item in data
            )
        ):
            # String and typed warning arrays can be logging, but cannot validate
            # an empty fallback. Malformed source arrays still count as failures.
            log_arrays = True
        elif data != []:
            failures += 1
    failures += int(log_arrays and not reports)
    return (reports if reports or empty_report else None), failures


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

    failures = 0
    try:
        data: Any = json.loads(output)
    except json.JSONDecodeError:
        data, failures = _extract_json_array(output)
        if data is None:
            return issues, max(1, failures)

    if not isinstance(data, list):
        logger.debug("Stylelint output is not a JSON array")
        return issues, 1

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

"""Parser for Stylelint JSON output.

Handles the ``--formatter json`` output produced by stylelint. The formatter
emits a JSON array with one object per linted source file, each carrying a
``warnings`` list (rule violations and ``CssSyntaxError`` parse failures) plus
``parseErrors`` and ``invalidOptionWarnings`` metadata arrays.
"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from lintro.parsers.stylelint.stylelint_issue import StylelintIssue


def _looks_like_stylelint_payload(data: Any) -> bool:
    """Return True when ``data`` matches Stylelint's per-file result array.

    Args:
        data: A decoded JSON value.

    Returns:
        True if ``data`` is a list of objects that look like Stylelint file
        results (``source`` / ``warnings`` / ``parseErrors`` keys).
    """
    if not isinstance(data, list):
        return False
    if not data:
        return True
    stylelint_keys = {"source", "warnings", "parseErrors", "invalidOptionWarnings"}
    for item in data:
        if not isinstance(item, dict):
            return False
        if stylelint_keys.intersection(item.keys()):
            return True
    return False


def _extract_json_array(output: str) -> str | None:
    """Extract the Stylelint JSON array substring from mixed tool output.

    Stylelint writes its JSON payload to stderr, which lintro combines with
    stdout. Skip non-Stylelint JSON arrays (e.g. ``["warning"]`` noise) and
    bracketed log lines so the real payload is selected.

    Args:
        output: The combined stdout/stderr from stylelint.

    Returns:
        The JSON array substring, or None if no Stylelint array is found.
    """
    decoder = json.JSONDecoder()
    idx = output.find("[")
    while idx != -1:
        try:
            data, end = decoder.raw_decode(output, idx)
        except (json.JSONDecodeError, ValueError):
            idx = output.find("[", idx + 1)
            continue
        if _looks_like_stylelint_payload(data):
            return output[idx:end]
        idx = output.find("[", idx + 1)
    return None


def parse_stylelint_output(output: str | None) -> list[StylelintIssue]:
    """Parse Stylelint JSON output into a list of StylelintIssue objects.

    Args:
        output: The raw combined output from stylelint ``--formatter json``.

    Returns:
        List of StylelintIssue objects. Returns an empty list for empty,
        None, or unparseable input.
    """
    issues: list[StylelintIssue] = []

    if not output or not output.strip():
        return issues

    json_content = _extract_json_array(output)
    if json_content is None:
        return issues

    try:
        data: Any = json.loads(json_content)
    except json.JSONDecodeError as exc:
        logger.debug(f"Failed to parse Stylelint JSON output: {exc}")
        return issues

    if not isinstance(data, list):
        logger.debug("Stylelint output is not a JSON array")
        return issues

    for source_result in data:
        if not isinstance(source_result, dict):
            continue
        source = str(source_result.get("source", "") or "")
        issues.extend(_parse_source_result(source_result, source))

    return issues


def _parse_source_result(
    source_result: dict[str, Any],
    source: str,
) -> list[StylelintIssue]:
    """Parse a single per-file result object into issues.

    Args:
        source_result: One entry from the stylelint JSON array.
        source: Resolved source file path for the entry.

    Returns:
        List of StylelintIssue objects for this source file.
    """
    issues: list[StylelintIssue] = []

    warnings = source_result.get("warnings", [])
    if isinstance(warnings, list):
        for warning in warnings:
            issue = _parse_warning(warning, source)
            if issue is not None:
                issues.append(issue)

    parse_errors = source_result.get("parseErrors", [])
    if isinstance(parse_errors, list):
        for parse_error in parse_errors:
            issue = _parse_warning(parse_error, source, default_rule="parseError")
            if issue is not None:
                issues.append(issue)

    # Invalid rule options are configuration errors stylelint reports per
    # file; dropping them would silently skip the affected rules.
    invalid_options = source_result.get("invalidOptionWarnings", [])
    if isinstance(invalid_options, list):
        for invalid_option in invalid_options:
            issue = _parse_warning(
                invalid_option,
                source,
                default_rule="invalidOption",
            )
            if issue is not None:
                issues.append(issue)

    return issues


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

r"""Parser for html-validate JSON output.

html-validate emits no SARIF formatter (available formatters: json, checkstyle,
codeframe, stylish, text), so its native JSON is parsed directly. The JSON shape
is an array of file results::

    [
      {
        "filePath": "index.html",
        "messages": [
          {
            "ruleId": "wcag/h37",
            "severity": 2,
            "message": "<img> is missing required \"alt\" attribute",
            "line": 5,
            "column": 2,
            "selector": "html > body > img",
            "ruleUrl": "https://html-validate.org/rules/wcag/h37.html"
          }
        ],
        "errorCount": 1,
        "warningCount": 0
      }
    ]
"""

from __future__ import annotations

import json

from lintro.parsers.base_parser import extract_optional_str
from lintro.parsers.html_validate.html_validate_issue import HtmlValidateIssue

# html-validate encodes severity numerically: 2 -> error, 1 -> warning.
_SEVERITY_MAP: dict[int, str] = {2: "error", 1: "warning"}


def _coerce_int(value: object) -> int:
    """Coerce a JSON value to an int, returning 0 when not convertible.

    Args:
        value: Raw value from the parsed JSON (may be int, str, or None).

    Returns:
        The integer value, or 0 when the value cannot be converted.
    """
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return 0


def parse_html_validate_output(output: str | None) -> list[HtmlValidateIssue]:
    """Return html-validate findings through the legacy list API.

    Args:
        output: Raw JSON formatter output.

    Returns:
        All usable findings, omitting parser failure accounting.
    """
    issues, _failures = parse_html_validate_output_with_stats(output)
    return issues


def parse_html_validate_output_with_stats(
    output: str | None,
) -> tuple[list[HtmlValidateIssue], int]:
    """Parse html-validate JSON output into a list of issues.

    Args:
        output: Raw stdout from ``html-validate --formatter json``. May be
            ``None`` or empty when the tool reports no issues.

    Returns:
        Usable findings and the count of invalid report containers or records.
    """
    issues: list[HtmlValidateIssue] = []

    if not output or not output.strip():
        return issues, 0

    try:
        data = json.loads(output)
    except (json.JSONDecodeError, ValueError):
        return issues, 1

    if not isinstance(data, list):
        return issues, 1

    failures = 0
    for file_result in data:
        if not isinstance(file_result, dict):
            failures += 1
            continue
        file_path = extract_optional_str(file_result, "filePath")
        messages = file_result.get("messages")
        if not isinstance(messages, list):
            failures += 1
            continue

        for message in messages:
            if not isinstance(message, dict):
                failures += 1
                continue

            raw_severity = message.get("severity")
            severity = _SEVERITY_MAP.get(
                raw_severity if isinstance(raw_severity, int) else -1,
                "warning",
            )
            issues.append(
                HtmlValidateIssue(
                    file=file_path,
                    line=_coerce_int(message.get("line")),
                    column=_coerce_int(message.get("column")),
                    message=extract_optional_str(message, "message"),
                    code=extract_optional_str(message, "ruleId"),
                    severity=severity,
                    selector=extract_optional_str(message, "selector"),
                    doc_url=extract_optional_str(message, "ruleUrl"),
                ),
            )

    return issues, failures

"""Parser for Vale JSON output.

Vale (``vale --output=JSON``) emits a JSON object keyed by file path. Each
value is a list of alert objects with this shape::

    {
      "docs/example.md": [
        {
          "Action": {"Name": "edit", "Params": ["truncate", " "]},
          "Span": [1, 7],
          "Check": "Vale.Repetition",
          "Description": "",
          "Link": "https://...",
          "Message": "'the' is repeated!",
          "Severity": "error",
          "Match": "The the",
          "Line": 3
        }
      ]
    }

When Vale cannot find a configuration file it emits a single top-level error
object (with ``Code`` ``E100``) instead of the per-file mapping; that case is
handled by the plugin (graceful skip), so this parser simply returns ``[]`` for
any payload that is not the per-file mapping shape.
"""

from __future__ import annotations

import json

from lintro.parsers.vale.vale_issue import ValeIssue


def _coerce_int(value: object) -> int:
    """Coerce a JSON value to a non-negative int, defaulting to 0.

    Args:
        value: Raw value from the parsed JSON payload.

    Returns:
        The value as an int when convertible and non-negative, else 0.
    """
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value if value > 0 else 0
    return 0


def parse_vale_output(output: str | None) -> list[ValeIssue]:
    """Parse Vale JSON output into a list of ``ValeIssue`` objects.

    Args:
        output: Raw stdout from ``vale --output=JSON``. May be ``None`` or
            empty when Vale reports no issues.

    Returns:
        A list of ``ValeIssue`` objects. Returns an empty list for empty,
        null, malformed, or non-mapping payloads (e.g., Vale's ``E100``
        no-config error object).
    """
    if not output or not output.strip():
        return []

    try:
        data = json.loads(output)
    except (json.JSONDecodeError, ValueError):
        return []

    # Expected shape is an object mapping file paths to alert lists. Vale's
    # runtime-error payload is a single object (not a mapping of lists), which
    # we intentionally ignore here.
    if not isinstance(data, dict):
        return []

    issues: list[ValeIssue] = []
    for file_path, alerts in data.items():
        if not isinstance(alerts, list):
            continue
        for alert in alerts:
            if not isinstance(alert, dict):
                continue

            check = str(alert.get("Check", "") or "")
            style = check.split(".", 1)[0] if "." in check else check

            span = alert.get("Span")
            column = 0
            if isinstance(span, list) and span:
                column = _coerce_int(span[0])

            link = alert.get("Link")
            doc_url = str(link) if link else ""

            issues.append(
                ValeIssue(
                    file=str(file_path),
                    line=_coerce_int(alert.get("Line")),
                    column=column,
                    message=str(alert.get("Message", "") or ""),
                    check=check,
                    style=style,
                    severity=str(alert.get("Severity", "") or ""),
                    match=str(alert.get("Match", "") or ""),
                    doc_url=doc_url,
                ),
            )

    return issues

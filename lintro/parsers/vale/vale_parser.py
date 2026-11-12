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

When Vale cannot run (no configuration, a missing style, a bad flag) it emits
a single top-level runtime-error object instead of the per-file mapping, with
scalar ``Code`` (``E100``, ``E201``, ...) and ``Text`` keys. That object is one
failed report, not one failure per key: :func:`vale_runtime_error` names it so
the plugin can skip (``E100``) or report an execution failure (#3352).
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


def _runtime_error(data: object) -> str | None:
    """Name Vale's runtime-error object, if ``data`` is one.

    Args:
        data: The decoded top-level JSON value.

    Returns:
        ``"<Code>: <Text>"`` for a runtime-error object, otherwise None. A
        per-file mapping never has a scalar ``Code`` string beside ``Text``.
    """
    if not isinstance(data, dict):
        return None
    code = data.get("Code")
    text = data.get("Text")
    if not isinstance(code, str) or not code or not isinstance(text, str):
        return None
    return f"{code}: {text}" if text else code


def vale_runtime_error(output: str | None) -> str | None:
    """Name the runtime error Vale printed instead of a report.

    Args:
        output: Raw output from ``vale --output=JSON``.

    Returns:
        ``"<Code>: <Text>"`` when the output is Vale's runtime-error object,
        otherwise None.
    """
    if not output or not output.strip():
        return None
    try:
        data = json.loads(output)
    except (json.JSONDecodeError, ValueError):
        return None
    return _runtime_error(data)


def parse_vale_output(output: str | None) -> list[ValeIssue]:
    """Return Vale findings through the legacy list API.

    Args:
        output: Raw JSON formatter output.

    Returns:
        All usable findings, omitting parser failure accounting.
    """
    issues, _failures = parse_vale_output_with_stats(output)
    return issues


def parse_vale_output_with_stats(
    output: str | None,
) -> tuple[list[ValeIssue], int]:
    """Parse Vale JSON output into a list of ``ValeIssue`` objects.

    Args:
        output: Raw stdout from ``vale --output=JSON``. May be ``None`` or
            empty when Vale reports no issues.

    Returns:
        Usable findings and the count of invalid report containers or records.
        The plugin handles Vale's no-config error separately.
    """
    if not output or not output.strip():
        return [], 0

    try:
        data = json.loads(output)
    except (json.JSONDecodeError, ValueError):
        return [], 1

    # Expected shape is an object mapping file paths to alert lists. Vale's
    # runtime-error object is one failed report, counted once rather than once
    # per scalar key (#3352).
    if not isinstance(data, dict) or _runtime_error(data) is not None:
        return [], 1

    issues: list[ValeIssue] = []
    failures = 0
    for file_path, alerts in data.items():
        if not isinstance(alerts, list):
            failures += 1
            continue
        for alert in alerts:
            if not isinstance(alert, dict):
                failures += 1
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

    return issues, failures

"""Parser for golangci-lint JSON output.

golangci-lint (v2) emits a single JSON document via
``--output.json.path stdout`` with the shape::

    {
      "Issues": [
        {
          "FromLinter": "errcheck",
          "Text": "Error return value of `os.Open` is not checked",
          "Severity": "",
          "Pos": {"Filename": "main.go", "Line": 9, "Column": 9},
          "SuggestedFixes": [...]        # optional; presence => fixable
        }
      ],
      "Report": {"Linters": [...]}
    }

A native parser is used rather than golangci-lint's SARIF output because the
SARIF export is lossy for lintro's model: it hard-codes ``level: "error"`` for
every finding (dropping the per-issue ``Severity``), omits ``rules[]``/``helpUri``
(no doc URLs), and carries no ``fixes[]`` (dropping ``SuggestedFixes`` /
autofix metadata). See ``docs/tool-analysis/golangci-lint-analysis.md``.
"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from lintro.parsers.base_parser import strip_ansi_codes
from lintro.parsers.golangci_lint.golangci_lint_issue import GolangciLintIssue


def _parse_issue(item: dict[str, Any]) -> GolangciLintIssue | None:
    """Convert a single golangci-lint issue object into a ``GolangciLintIssue``.

    Args:
        item: A single entry from the ``Issues`` array.

    Returns:
        A populated ``GolangciLintIssue`` or ``None`` when the entry cannot be
        parsed.
    """
    try:
        message_text = str(item.get("Text", "")).strip()
        if not message_text:
            return None

        linter = str(item.get("FromLinter", "")).strip()

        pos = item.get("Pos", {})
        if not isinstance(pos, dict):
            pos = {}

        file_name = pos.get("Filename")
        if not file_name or not isinstance(file_name, str):
            # Package-level build/config/analysis failures carry no position.
            # Keep them visible with a placeholder rather than silently
            # dropping a real failure from the report.
            file_name = "(module)"

        line_val = pos.get("Line")
        column_val = pos.get("Column")
        line = int(line_val) if line_val is not None else 0
        column = int(column_val) if column_val is not None else 0

        severity_raw = item.get("Severity")
        severity = str(severity_raw).strip() if severity_raw else None

        # golangci-lint exposes autofixes under either ``SuggestedFixes``
        # (v2 analyzer edits) or ``Replacement`` (legacy). Either signals
        # that the finding is fixable via ``--fix``.
        fixable = bool(item.get("SuggestedFixes")) or bool(item.get("Replacement"))

        return GolangciLintIssue(
            file=file_name,
            line=line,
            column=column,
            code=linter,
            message=message_text,
            level=severity,
            fixable=fixable,
        )
    except (KeyError, TypeError, ValueError) as e:
        logger.debug(f"Failed to parse golangci-lint issue: {e}")
        return None


def parse_golangci_lint_output(output: str | None) -> list[GolangciLintIssue]:
    """Parse golangci-lint JSON output into ``GolangciLintIssue`` objects.

    Args:
        output: Raw stdout emitted by ``golangci-lint run`` with
            ``--output.json.path stdout``. May be ``None``.

    Returns:
        A list of ``GolangciLintIssue`` instances. Returns an empty list when
        there are no issues or the output cannot be decoded.
    """
    if not output or not output.strip():
        return []

    # Strip ANSI codes for consistent parsing across environments.
    text = strip_ansi_codes(output).strip()

    # golangci-lint may append a human-readable stats footer after the JSON
    # document. Decode the leading JSON object and ignore any trailing text.
    start = text.find("{")
    if start == -1:
        return []

    try:
        data, _ = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError as e:
        logger.debug(f"Failed to decode golangci-lint output: {e}")
        return []

    if not isinstance(data, dict):
        return []

    raw_issues = data.get("Issues")
    if not isinstance(raw_issues, list):
        return []

    issues: list[GolangciLintIssue] = []
    for item in raw_issues:
        if not isinstance(item, dict):
            continue
        parsed = _parse_issue(item)
        if parsed is not None:
            issues.append(parsed)

    return issues

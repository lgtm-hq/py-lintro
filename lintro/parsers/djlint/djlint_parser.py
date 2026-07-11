"""Parser for djLint CLI output.

djLint emits two distinct output shapes depending on mode:

- Lint mode (``--lint``) prints one finding per line, by default in the form::

      H013 2:0 Img tag should have an alt attribute. <img src="a.png">

  A custom ``--linter-output-format`` can move the file path to the front::

      template.html 2:0 H013 Img tag should have an alt attribute.

- Check/reformat mode (``--check`` / ``--reformat``) prints a per-file header
  (the file path followed by a box-drawing rule) and a unified diff, ending
  with a summary such as ``1 file would be updated.``

This parser normalizes both shapes into :class:`DjlintIssue` objects. Rule
findings are surfaced with their code and marked non-fixable; formatting diffs
are surfaced per file and marked fixable, since ``djlint --reformat`` applies
them automatically.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from lintro.parsers.base_parser import strip_ansi_codes
from lintro.parsers.djlint.djlint_issue import DjlintIssue

# Lint finding with the default output order: "H013 2:0 message [match]".
_LINT_LINE_RE: re.Pattern[str] = re.compile(
    r"^(?P<code>[A-Z]\d{3})\s+(?P<line>\d+):(?P<col>\d+)\s+(?P<msg>.+?)\s*$",
)

# Lint finding with a leading file path (custom --linter-output-format):
# "template.html 2:0 H013 message".
_LINT_LINE_WITH_FILE_RE: re.Pattern[str] = re.compile(
    r"^(?P<file>\S+)\s+(?P<line>\d+):(?P<col>\d+)\s+"
    r"(?P<code>[A-Z]\d{3})\s+(?P<msg>.+?)\s*$",
)

# The box-drawing rule djLint prints under each file header in check/reformat
# mode. Tolerates ASCII hyphens as a fallback for stripped environments.
_BOX_RE: re.Pattern[str] = re.compile(r"^[─\-]{3,}$")

# Summary line printed in check/reformat mode.
_SUMMARY_RE: re.Pattern[str] = re.compile(
    r"(?P<count>\d+)\s+files?\s+would\s+be\s+updated",
    re.IGNORECASE,
)

# Progress/status banners that must not be mistaken for file headers.
_STATUS_RE: re.Pattern[str] = re.compile(
    r"^(?:Checking|Linting|Reformatting|Formatting)\b",
    re.IGNORECASE,
)


def _iter_nonempty_lines(text: str) -> Iterable[str]:
    """Iterate stripped, non-empty lines from a text block.

    Args:
        text: Input text to split into lines.

    Yields:
        str: Non-empty lines stripped of surrounding whitespace.
    """
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            yield stripped


def _parse_lint_findings(lines: list[str]) -> list[DjlintIssue]:
    """Parse rule-based lint findings from djLint output lines.

    Args:
        lines: Stripped, non-empty output lines.

    Returns:
        list[DjlintIssue]: Rule findings, each marked non-fixable.
    """
    issues: list[DjlintIssue] = []
    for line in lines:
        match = _LINT_LINE_WITH_FILE_RE.match(line) or _LINT_LINE_RE.match(line)
        if not match:
            continue
        groups = match.groupdict()
        issues.append(
            DjlintIssue(
                file=groups.get("file") or "",
                line=int(groups["line"]),
                column=int(groups["col"]),
                code=groups["code"],
                message=groups["msg"].strip(),
                fixable=False,
            ),
        )
    return issues


def _parse_formatting_diffs(lines: list[str]) -> list[DjlintIssue]:
    """Parse per-file formatting diffs from djLint check/reformat output.

    djLint prints each file's path immediately before a box-drawing rule.
    The path preceding each rule is surfaced as one fixable formatting issue.

    Args:
        lines: Stripped, non-empty output lines.

    Returns:
        list[DjlintIssue]: One fixable formatting issue per reformatted file.
    """
    issues: list[DjlintIssue] = []
    previous: str | None = None
    for line in lines:
        if _BOX_RE.match(line):
            if previous and not _STATUS_RE.match(previous):
                issues.append(
                    DjlintIssue(
                        file=previous,
                        message="File would be reformatted",
                        fixable=True,
                    ),
                )
            continue
        previous = line
    return issues


def parse_djlint_output(output: str | None) -> list[DjlintIssue]:
    """Parse raw djLint output into structured issues.

    Rule-based lint findings take precedence: when present they are returned
    directly. Otherwise the output is treated as check/reformat mode and
    per-file formatting diffs are extracted, falling back to the summary count
    when individual file headers are unavailable.

    Args:
        output: Raw stdout/stderr combined output from djLint.

    Returns:
        list[DjlintIssue]: Parsed issues from the tool output.
    """
    if not output:
        return []

    output = strip_ansi_codes(output)
    lines = list(_iter_nonempty_lines(output))

    lint_issues = _parse_lint_findings(lines)
    if lint_issues:
        return lint_issues

    formatting_issues = _parse_formatting_diffs(lines)
    if formatting_issues:
        return formatting_issues

    # Fallback: only a summary line was emitted (no per-file headers).
    summary = _SUMMARY_RE.search(output)
    if summary:
        count = int(summary.group("count"))
        return [
            DjlintIssue(
                file="<unknown>",
                message="File would be reformatted",
                fixable=True,
            )
            for _ in range(count)
        ]

    return []

"""Parser for dotenv-linter output.

dotenv-linter emits plain-text diagnostics (one per line) in the format::

    filename:line CheckName: message

It also prints non-diagnostic lines such as ``Checking <file>`` headers and a
trailing ``Found N problems`` / ``No problems found`` summary. Those lines do
not match the diagnostic pattern and are ignored.

dotenv-linter does not emit SARIF or JSON output in any released version, so
this native line parser is required to capture the check name, line number,
and message with full fidelity.
"""

from __future__ import annotations

import re

from lintro.parsers.base_parser import strip_ansi_codes
from lintro.parsers.dotenv_linter.dotenv_linter_issue import DotenvLinterIssue

# Diagnostic line: ``filename:line CheckName: message``.
# The filename is matched non-greedily up to the ``:<line>`` marker that is
# followed by whitespace, so paths containing colons remain unambiguous.
_LINE_RE: re.Pattern[str] = re.compile(
    r"^(?P<file>.+?):(?P<line>\d+)\s+(?P<code>[A-Za-z]+):\s*(?P<msg>.*)$",
)


def parse_dotenv_linter_output(output: str | None) -> list[DotenvLinterIssue]:
    """Parse dotenv-linter output into a list of ``DotenvLinterIssue`` objects.

    Args:
        output: Raw stdout/stderr from dotenv-linter, or None. May include
            header and summary lines, which are ignored.

    Returns:
        List of parsed ``DotenvLinterIssue`` objects. Returns an empty list
        when the output is None, empty, or contains no diagnostic lines.
    """
    if not output or not output.strip():
        return []

    issues: list[DotenvLinterIssue] = []
    for raw_line in strip_ansi_codes(output).splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = _LINE_RE.match(line)
        if not match:
            continue
        issues.append(
            DotenvLinterIssue(
                file=match.group("file"),
                line=int(match.group("line")),
                column=0,
                code=match.group("code"),
                message=match.group("msg").strip(),
            ),
        )
    return issues

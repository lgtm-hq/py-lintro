"""Parser for commitlint text output.

Commitlint does not emit SARIF and has no built-in JSON formatter (its
``--format`` flag loads an external formatter module), so lintro parses its
default human-readable report.

A report for one or more commits looks like:

    ⧗   --- input ---
    bad commit message
    ✖   subject may not be empty [subject-empty]
    ✖   type may not be empty [type-empty]
    ⚠   body's lines must not be longer than 100 characters [body-max-line-length]

    ✖   found 2 problems, 1 warnings

Each ``⧗   --- input ---`` block introduces one commit; the line immediately
following it is that commit's subject. Violation lines carry the human-readable
message and the rule name in trailing square brackets.
"""

import re

from lintro.parsers.base_parser import is_empty_output, strip_ansi_codes
from lintro.parsers.commitlint.commitlint_issue import CommitlintIssue

# Marker introducing a commit's input block. The subject follows on the next
# non-empty line.
_INPUT_MARKER: str = "--- input ---"

# Violation line: "<symbol>   <message> [<rule-name>]".
# ``✖`` marks an error, ``⚠`` marks a warning. The rule name is lower-case
# with hyphens/digits (e.g. ``subject-empty``, ``type-enum``).
_VIOLATION_PATTERN: re.Pattern[str] = re.compile(
    r"^\s*(?P<symbol>[✖⚠])\s+(?P<message>.+?)\s+"
    r"\[(?P<rule>[a-z0-9][a-z0-9-]*)\]\s*$",
)

_SYMBOL_ERROR: str = "✖"  # ✖
_SYMBOL_WARNING: str = "⚠"  # ⚠


def parse_commitlint_output(output: str | None) -> list[CommitlintIssue]:
    """Parse commitlint output into a list of CommitlintIssue objects.

    Args:
        output: Raw commitlint report text, may be ``None``.

    Returns:
        List of CommitlintIssue objects (empty when the output is empty,
        ``None``, or contains no rule violations).
    """
    issues: list[CommitlintIssue] = []

    if is_empty_output(output):
        return issues

    text = strip_ansi_codes(output)  # type: ignore[arg-type]
    lines: list[str] = text.splitlines()

    current_subject: str = ""
    expect_subject: bool = False

    for line in lines:
        stripped = line.strip()

        # The line following the input marker is the commit subject.
        if expect_subject:
            expect_subject = False
            if stripped:
                current_subject = stripped
                continue

        if _INPUT_MARKER in stripped:
            expect_subject = True
            current_subject = ""
            continue

        match = _VIOLATION_PATTERN.match(line)
        if not match:
            continue

        message = match.group("message").strip()
        rule = match.group("rule")
        symbol = match.group("symbol")

        # The trailing summary line ("found N problems, M warnings") never
        # carries a "[rule]" suffix, so it cannot match here — no filtering
        # of it is required.
        level = "warning" if symbol == _SYMBOL_WARNING else "error"

        issues.append(
            CommitlintIssue(
                file=current_subject,
                line=0,
                column=0,
                message=message,
                rule=rule,
                level=level,
            ),
        )

    return issues

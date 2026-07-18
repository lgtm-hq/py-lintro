"""Parser for typos JSON output.

typos emits newline-delimited JSON (``--format json``): one JSON object per
line. Objects have a ``type`` discriminator; only ``type == "typo"`` entries
describe a spelling finding. Other object types (for example ``error`` or
``binary_file`` diagnostics) are ignored so the parser only surfaces
actionable typos.
"""

from __future__ import annotations

import json

from lintro.parsers.typos.typos_issue import TyposIssue


def _build_message(typo: str, corrections: list[str]) -> str:
    """Compose a human-readable message for a typo finding.

    Args:
        typo: The misspelled word.
        corrections: Suggested replacement words.

    Returns:
        A message of the form ``"<typo>" should be "<correction>"``. When
        several corrections are offered they are comma-separated. When no
        corrections are available the message notes the word is disallowed.
    """
    if not corrections:
        return f'"{typo}" is disallowed'
    joined = ", ".join(f'"{c}"' for c in corrections)
    return f'"{typo}" should be {joined}'


def parse_typos_output(output: str | None) -> list[TyposIssue]:
    """Parse typos JSON output into issues.

    Args:
        output: Raw stdout from ``typos --format json`` (newline-delimited
            JSON), or None.

    Returns:
        List of parsed typo issues. Empty when the input is empty, None, or
        contains no ``typo`` entries. Malformed lines are skipped rather than
        raising.
    """
    if not output:
        return []

    issues: list[TyposIssue] = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(record, dict):
            continue
        if record.get("type") != "typo":
            continue

        path = record.get("path")
        typo = record.get("typo")
        if not isinstance(path, str) or not isinstance(typo, str):
            continue

        raw_corrections = record.get("corrections")
        corrections: list[str] = (
            [str(c) for c in raw_corrections]
            if isinstance(raw_corrections, list)
            else []
        )

        line_num = record.get("line_num")
        line_no = line_num if isinstance(line_num, int) else 0

        byte_offset = record.get("byte_offset")
        offset = byte_offset if isinstance(byte_offset, int) else 0

        issues.append(
            TyposIssue(
                file=path,
                line=line_no,
                # typos reports a 0-based byte offset; present it as a
                # 1-based column for display parity with other tools.
                column=offset + 1,
                message=_build_message(typo=typo, corrections=corrections),
                typo=typo,
                corrections=corrections,
                byte_offset=offset,
            ),
        )
    return issues

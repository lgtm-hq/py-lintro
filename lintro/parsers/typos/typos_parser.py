"""Parser for typos JSON output.

typos emits newline-delimited JSON (``--format json``): one JSON object per
line. Objects have a ``type`` discriminator; only ``type == "typo"`` entries
describe a spelling finding. Other object types (for example ``error`` or
``binary_file`` diagnostics) are ignored so the parser only surfaces
actionable typos.
"""

from __future__ import annotations

import json

from loguru import logger

from lintro.parsers.typos.typos_issue import TyposIssue


def _strict_int(value: object) -> int | None:
    """Return ``value`` when it is a real ``int``, else ``None``.

    JSON ``true``/``false`` decode to ``bool``, which is an ``int`` subclass
    and would otherwise slip through an ``isinstance`` check and produce
    nonsense such as ``line=True``.

    Args:
        value: Decoded JSON value to validate.

    Returns:
        The integer value, or None when it is missing or not a plain int.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


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
        record_type = record.get("type")
        if record_type != "typo":
            # typos also emits diagnostic records (``error``, ``binary_file``,
            # ``file_not_found``, ...). They are not lint findings, so they are
            # not turned into issues; they are logged instead. A run that only
            # produced diagnostics also exits non-zero, and the plugin fails
            # closed on a non-zero exit with no parsed findings, so nothing is
            # silently lost.
            logger.debug(f"typos: ignoring non-typo record of type {record_type!r}")
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

        # Values outside the valid range are treated as "unknown" (0), which
        # is what the formatter renders as a dash.
        line_value = _strict_int(record.get("line_num"))
        line_no = line_value if line_value is not None and line_value > 0 else 0

        offset_value = _strict_int(record.get("byte_offset"))
        if offset_value is not None and offset_value >= 0:
            offset = offset_value
            # typos reports a 0-based byte offset; present it as a 1-based
            # column for display parity with other tools.
            column = offset + 1
        else:
            # An absent or invalid offset stays "unknown" rather than pointing
            # at the first character of the line.
            offset = 0
            column = 0

        issues.append(
            TyposIssue(
                file=path,
                line=line_no,
                column=column,
                message=_build_message(typo=typo, corrections=corrections),
                typo=typo,
                corrections=corrections,
                byte_offset=offset,
                # typos can only auto-replace a finding when it offers at least
                # one correction; a word banned through config has none.
                fixable=bool(corrections),
            ),
        )
    return issues

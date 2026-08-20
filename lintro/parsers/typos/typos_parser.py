"""Parser for typos JSON output.

typos emits newline-delimited JSON (``--format json``): one JSON object per
line, with a ``type`` discriminator.

The stream mixes two different kinds of record, so this module exposes two
parsers rather than one:

* :func:`parse_typos_output` returns the ``type == "typo"`` entries — the lint
  findings.
* :func:`parse_typos_errors` returns the ``type == "error"`` entries — per-file
  diagnostics such as ``Permission denied``. These can appear in the *same*
  run as real findings (one unreadable file among many), so the plugin tracks
  them separately and fails the run on them instead of inferring failure from
  an empty findings list.

Only a small allowlist of record types is treated as informational; anything
else is reported as a diagnostic, so a type introduced by a future typos
release fails loudly rather than vanishing.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

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


# typos record types that are informational only: they describe what typos did
# with a file, not a problem the user must act on. Anything else that is not a
# ``typo`` is treated as a diagnostic, so a record type added by a future typos
# release fails loudly rather than vanishing.
_INFORMATIONAL_RECORD_TYPES: frozenset[str] = frozenset({"binary_file", "file_type"})


def _iter_records(output: str | None) -> Iterator[tuple[dict[str, Any] | None, str]]:
    """Iterate the newline-delimited JSON records in typos' stdout.

    Both public parsers walk the same stream, so the decoding lives here: two
    hand-rolled loops previously drifted apart on which lines they skipped.

    Args:
        output: Raw stdout from ``typos --format json``, or None.

    Yields:
        tuple[dict[str, Any] | None, str]: ``(record, raw_line)`` pairs.
            ``record`` is the decoded object, or None when the line was not a
            JSON object — callers decide whether that is a skip or a
            diagnostic.
    """
    if not output:
        return
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            record = json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            yield None, stripped
            continue
        if not isinstance(record, dict):
            yield None, stripped
            continue
        yield record, stripped


def parse_typos_errors(output: str | None) -> list[str]:
    """Extract typos' diagnostics from its JSON output.

    typos reports per-file problems (unreadable file, decode failure, ...) as
    ``{"type": "error", "path": ..., "msg": ...}`` records interleaved with the
    ``typo`` findings on stdout. Those must be surfaced as tool failures even
    when the same run also reported real typos for other files, so they are
    parsed separately from :func:`parse_typos_output`.

    The check is deliberately fail-closed: any record that is neither a
    ``typo`` nor a known informational type counts as a diagnostic, and so does
    a line that is not decodable JSON at all (with ``--format json`` everything
    on stdout should be).

    Args:
        output: Raw stdout from ``typos --format json``, or None.

    Returns:
        One human-readable message per diagnostic, in output order. Empty when
        the input is empty, None, or contains only findings.
    """
    messages: list[str] = []
    for record, raw in _iter_records(output):
        if record is None:
            messages.append(f"unparseable typos output: {raw}")
            continue
        record_type = record.get("type")
        if record_type == "typo" or record_type in _INFORMATIONAL_RECORD_TYPES:
            continue
        msg = record.get("msg") or record.get("error")
        path = record.get("path")
        detail = str(msg) if msg is not None else f"typos reported {record_type!r}"
        messages.append(f"{path}: {detail}" if isinstance(path, str) else detail)
    return messages


def parse_typos_output(output: str | None) -> list[TyposIssue]:
    """Parse typos JSON output into issues.

    Only ``type == "typo"`` records become issues. Diagnostics on the same
    stream are not represented here at all; :func:`parse_typos_errors` returns
    them and the plugin fails the run on them, so an error interleaved with
    real findings is never silently dropped.

    Args:
        output: Raw stdout from ``typos --format json`` (newline-delimited
            JSON), or None.

    Returns:
        List of parsed typo issues. Empty when the input is empty, None, or
        contains no ``typo`` entries. Malformed lines are skipped rather than
        raising.
    """
    issues: list[TyposIssue] = []
    for record, _raw in _iter_records(output):
        if record is None:
            continue
        record_type = record.get("type")
        if record_type != "typo":
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

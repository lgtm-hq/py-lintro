"""Parser for Black output.

Black commonly emits terse messages like:
- "would reformat foo.py" (check mode with --check)
- "reformatted foo.py" (fix mode)
- a summary line like "1 file would be reformatted" or
  "2 files reformatted" (with no per-file lines in some environments).

We normalize items into ``BlackIssue`` objects so the table formatter can
render consistent rows. When only a summary is present, we synthesize one
``BlackIssue`` per counted file with ``file`` set to "<unknown>" so totals
remain accurate across environments.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from loguru import logger

from lintro.parsers.base_parser import strip_ansi_codes
from lintro.parsers.black.black_issue import BlackIssue

_WOULD_REFORMAT = re.compile(r"^would reformat\s+(?P<file>.+)$", re.IGNORECASE)
_REFORMATTED = re.compile(r"^reformatted\s+(?P<file>.+)$", re.IGNORECASE)
_SUMMARY_WOULD = re.compile(
    r"(?P<count>\d+)\s+file(?:s)?\s+would\s+be\s+reformatted\.?",
    re.IGNORECASE,
)
_SUMMARY_REFORMATTED = re.compile(
    r"(?P<count>\d+)\s+file(?:s)?\s+reformatted\.?",
    re.IGNORECASE,
)


def _iter_issue_lines(lines: Iterable[str]) -> Iterable[str]:
    for line in lines:
        s = line.strip()
        if not s:
            continue
        yield s


def parse_black_output(output: str, *, applied: bool = False) -> list[BlackIssue]:
    """Parse Black CLI output into a list of ``BlackIssue`` objects.

    Args:
        output: Raw stdout+stderr from a Black invocation.
        applied: When True, the parse is happening in an applied-changes
            (fix) context, so preview-tense ``black --check`` phrasing
            ("would reformat foo.py") is normalized to the past-tense
            "Reformatted file" message. Defaults to False (check mode).

    Returns:
        list[BlackIssue]: Per-file issues indicating formatting diffs. If only
        a summary is present (no per-file lines), returns a synthesized list
        sized to the summary count with ``file`` set to "<unknown>".
    """
    if not output:
        return []

    would_reformat_message = "Reformatted file" if applied else "Would reformat file"

    # Strip ANSI codes for consistent parsing across environments
    output = strip_ansi_codes(output)

    issues: list[BlackIssue] = []
    try:
        for line in _iter_issue_lines(output.splitlines()):
            try:
                m = _WOULD_REFORMAT.match(line)
                if m:
                    file_match = m.group("file")
                    if file_match:
                        issues.append(
                            BlackIssue(file=file_match, message=would_reformat_message),
                        )
                    continue
                m = _REFORMATTED.match(line)
                if m:
                    file_match = m.group("file")
                    if file_match:
                        issues.append(
                            BlackIssue(file=file_match, message="Reformatted file"),
                        )
                    continue
            except (AttributeError, IndexError) as e:
                logger.debug(f"Failed to parse black line '{line}': {e}")
                continue

        # Some environments (e.g., CI) may emit only a summary line without listing
        # per-file entries. In that case, synthesize issues so counts remain
        # consistent across environments.
        if not issues:
            m_sum = _SUMMARY_WOULD.search(output)
            if not m_sum:
                m_sum = _SUMMARY_REFORMATTED.search(output)
            if m_sum:
                try:
                    count = int(m_sum.group("count"))
                except (ValueError, TypeError, AttributeError) as e:
                    logger.debug(f"Failed to parse black summary count: {e}")
                    count = 0
                if count > 0:
                    logger.info(
                        f"Black reported {count} file(s) need formatting but "
                        "didn't list them. Run 'black --check .' directly to "
                        "see affected files.",
                    )
                    for _ in range(count):
                        issues.append(
                            BlackIssue(
                                file="<unknown>",
                                message="Formatting change detected",
                            ),
                        )
    except (ValueError, KeyError, TypeError, IndexError) as e:
        logger.debug(f"Error parsing black output: {e}")

    return issues

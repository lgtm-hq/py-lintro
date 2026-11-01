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
from pathlib import Path

from loguru import logger

from lintro.parsers.base_parser import strip_ansi_codes
from lintro.parsers.black.black_issue import BlackIssue

_WOULD_REFORMAT = re.compile(r"^would reformat\s+(?P<file>.+)$", re.IGNORECASE)
_REFORMATTED = re.compile(r"^reformatted\s+(?P<file>.+)$", re.IGNORECASE)
_UNCHANGED = re.compile(
    r"^(?P<file>.+) (?:already well formatted, good job\."
    r"|wasn't modified on disk since last run\.)$",
)
# Native summaries occupy a whole line; filenames can contain the same words.
_SUMMARY_WOULD = re.compile(
    r"^[ \t]*(?P<count>\d+) files? would be reformatted"
    r"(?:, \d+ files? would be left unchanged)?"
    r"(?:, \d+ files? would fail to reformat)?\.?[ \t]*\r?$",
    re.IGNORECASE | re.MULTILINE,
)
_SUMMARY_REFORMATTED = re.compile(
    r"^[ \t]*(?P<count>\d+) files? reformatted"
    r"(?:, \d+ files? left unchanged)?"
    r"(?:, \d+ files? failed to reformat)?\.?[ \t]*\r?$",
    re.IGNORECASE | re.MULTILINE,
)
_VERBOSE_METADATA = re.compile(
    r"^(?:Identified `.+` as project root containing a .+\."
    r"|Using configuration (?:from .+|in .+)\."
    r"|[a-z_]+: .*"
    r'|Found input source: ".+"'
    r"|.+ ignored: .+"
    r"|(?:All done!|Oh no!).*"
    r"|\d+ files? would be left unchanged\.?)$",
)


def black_report_parse_failures(output: str, issues: list[BlackIssue]) -> int:
    """Count unaccounted formatting entries in Black's native summary.

    Args:
        output: Native Black output.
        issues: Parsed per-file formatting findings.

    Returns:
        Missing entries, including summary-only findings without file identities.
    """
    output = strip_ansi_codes(output)
    summary = _SUMMARY_WOULD.search(output) or _SUMMARY_REFORMATTED.search(output)
    expected = int(summary.group("count")) if summary else len(issues)
    identified = sum(issue.file != "<unknown>" for issue in issues)
    return abs(expected - identified)


def black_inspected_paths(
    output: str,
    *,
    files: list[str],
    cwd: str | None,
) -> frozenset[str] | None:
    """Read affirmative per-file confirmations from a completed verbose check.

    Discovery, exclusion messages and summary counts do not prove inspection.
    Cached unchanged confirmations are Black's native verdict for that file.
    Only invocation identities with unambiguous single-line reports grant coverage.

    Args:
        output: Complete native verbose check report.
        files: Exact file arguments passed to Black, relative to cwd.
        cwd: Working directory used by Black.

    Returns:
        Exact confirmed files, or None for ambiguous or unresolvable reports.
    """
    paths: set[str] = set()
    try:
        base = Path(cwd or Path.cwd())
        expected: set[Path] = set()
        for file in files:
            path = base / file
            resolved = path.resolve()
            # splitlines also recognizes CR and Unicode line separators. Check
            # both spellings because Black may resolve symlink input paths.
            if any(
                name.splitlines() != [name] or strip_ansi_codes(name) != name
                for name in (str(path), str(resolved))
            ):
                return None
            expected.add(resolved)
        for line in strip_ansi_codes(output).splitlines():
            match = _WOULD_REFORMAT.fullmatch(line) or _UNCHANGED.fullmatch(line)
            if match is None:
                if line.strip() and not (
                    _VERBOSE_METADATA.fullmatch(line) or _SUMMARY_WOULD.fullmatch(line)
                ):
                    return None
                continue
            path = (base / match.group("file")).resolve()
            if path not in expected or str(path) in paths or not path.is_file():
                return None
            paths.add(str(path))
    except (OSError, ValueError, RuntimeError):
        return None
    return frozenset(paths)


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

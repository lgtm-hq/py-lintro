"""Parser for cppcheck XML output.

Cppcheck emits its structured report as XML (``--xml``, schema version 2) on
**stderr**, while human-readable progress goes to stdout. This parser consumes
that XML and preserves every field lintro can display, including the native
six-level severity, the CWE identifier, and the ``inconclusive`` flag.

Native XML is used rather than cppcheck's newer ``--output-format=sarif`` because
SARIF is lossy for cppcheck: it collapses the ``style``/``performance``/
``portability``/``information`` severities into a single ``warning`` level and
drops the ``inconclusive`` flag. See ``docs/tool-analysis/cppcheck-analysis.md``.
"""

from __future__ import annotations

import re

from defusedxml import ElementTree

from lintro.parsers.cppcheck.cppcheck_issue import CppcheckIssue

# Extracts the ``<results>...</results>`` document even when cppcheck interleaves
# other text (e.g. combined stdout/stderr capture) around it.
_RESULTS_BLOCK: re.Pattern[str] = re.compile(
    r"<results\b.*?</results>",
    re.DOTALL,
)


def _to_int(value: str | None) -> int:
    """Convert an XML attribute to an int, defaulting to 0.

    Args:
        value: Raw attribute string or None.

    Returns:
        Parsed integer, or 0 when the value is missing or non-numeric.
    """
    if not value:
        return 0
    try:
        return int(value)
    except ValueError:
        return 0


def parse_cppcheck_output(output: str | None) -> list[CppcheckIssue]:
    """Parse cppcheck XML output into a list of ``CppcheckIssue`` objects.

    Args:
        output: The raw cppcheck XML report (schema version 2). May be None,
            empty, or contain surrounding non-XML text.

    Returns:
        List of ``CppcheckIssue`` objects. Returns an empty list for missing,
        empty, or malformed input.
    """
    if not output or not output.strip():
        return []

    match = _RESULTS_BLOCK.search(output)
    if match is None:
        return []

    try:
        root = ElementTree.fromstring(match.group(0))
    except ElementTree.ParseError:
        return []

    issues: list[CppcheckIssue] = []

    errors = root.find("errors")
    if errors is None:
        return []

    for error in errors.findall("error"):
        code = error.get("id", "")
        severity = error.get("severity", "error")
        message = error.get("msg", "")
        cwe = _to_int(error.get("cwe"))
        inconclusive = error.get("inconclusive", "").lower() == "true"

        locations = error.findall("location")
        if locations:
            # Cppcheck lists the primary error site first, followed by
            # value-flow trace steps; report the primary location.
            primary = locations[0]
            file_path = primary.get("file", error.get("file0", ""))
            line = _to_int(primary.get("line"))
            column = _to_int(primary.get("column"))
        else:
            # Meta diagnostics (e.g. configuration notices) carry no location.
            file_path = error.get("file0", "")
            line = 0
            column = 0

        issues.append(
            CppcheckIssue(
                file=file_path,
                line=line,
                column=column,
                message=message,
                severity=severity,
                code=code,
                cwe=cwe,
                inconclusive=inconclusive,
            ),
        )

    return issues

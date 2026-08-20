"""Typos parser package.

Exports the issue type and combined parse function so imports match the layout
expected by ``skills/lintro-verify``. Findings-only and diagnostics-only
helpers stay private so a conventional ``parse_<tool>_output`` import cannot
treat a diagnostic stream as a clean scan.
"""

from lintro.parsers.typos.typos_issue import TyposIssue
from lintro.parsers.typos.typos_parser import TyposReport, parse_typos_report

__all__ = [
    "TyposIssue",
    "TyposReport",
    "parse_typos_report",
]

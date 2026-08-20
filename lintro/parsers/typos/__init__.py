"""Typos parser package.

Exports the issue type and parse function so imports match the layout expected
by ``skills/lintro-verify``.
"""

from lintro.parsers.typos.typos_issue import TyposIssue
from lintro.parsers.typos.typos_parser import (
    TyposReport,
    parse_typos_errors,
    parse_typos_output,
    parse_typos_report,
)

__all__ = [
    "TyposIssue",
    "TyposReport",
    "parse_typos_errors",
    "parse_typos_output",
    "parse_typos_report",
]

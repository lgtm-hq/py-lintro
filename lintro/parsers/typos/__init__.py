"""Typos parser package.

Exports the issue type and parse function so imports match the layout expected
by ``skills/lintro-verify``.
"""

from lintro.parsers.typos.typos_issue import TyposIssue
from lintro.parsers.typos.typos_parser import parse_typos_output

__all__ = [
    "TyposIssue",
    "parse_typos_output",
]

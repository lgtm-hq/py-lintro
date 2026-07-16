"""Stylelint parser package.

Exports issue types and the parse function so imports match ``lintro-verify``.
"""

from lintro.parsers.stylelint.stylelint_issue import StylelintIssue
from lintro.parsers.stylelint.stylelint_parser import parse_stylelint_output

__all__ = [
    "StylelintIssue",
    "parse_stylelint_output",
]

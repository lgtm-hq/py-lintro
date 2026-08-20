"""djLint parser package."""

from lintro.parsers.djlint.djlint_issue import DjlintIssue
from lintro.parsers.djlint.djlint_parser import parse_djlint_output

__all__ = ["DjlintIssue", "parse_djlint_output"]

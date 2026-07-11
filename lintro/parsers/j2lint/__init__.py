"""j2lint parser package."""

from lintro.parsers.j2lint.j2lint_issue import J2lintIssue
from lintro.parsers.j2lint.j2lint_parser import parse_j2lint_output

__all__ = ["J2lintIssue", "parse_j2lint_output"]

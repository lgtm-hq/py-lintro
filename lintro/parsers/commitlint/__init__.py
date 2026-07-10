"""Commitlint parser package.

Exposes the commitlint issue model and output parser.
"""

from lintro.parsers.commitlint.commitlint_issue import CommitlintIssue
from lintro.parsers.commitlint.commitlint_parser import parse_commitlint_output

__all__ = ["CommitlintIssue", "parse_commitlint_output"]

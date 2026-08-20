"""RuboCop parser package.

Exposes the issue model and JSON output parser for RuboCop, the Ruby static
code analyzer and formatter.
"""

from lintro.parsers.rubocop.rubocop_issue import RubocopIssue
from lintro.parsers.rubocop.rubocop_parser import parse_rubocop_output

__all__ = ["RubocopIssue", "parse_rubocop_output"]

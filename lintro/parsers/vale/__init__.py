"""Vale prose linter parser package.

Exposes the Vale issue model and output parser for use by the Vale tool
plugin and tests.
"""

from lintro.parsers.vale.vale_issue import ValeIssue
from lintro.parsers.vale.vale_parser import parse_vale_output

__all__ = ["ValeIssue", "parse_vale_output"]

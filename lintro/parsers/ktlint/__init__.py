"""ktlint parser module.

This module provides parsing functionality for ktlint output, an
anti-bikeshedding Kotlin linter with a built-in formatter.
"""

from lintro.parsers.ktlint.ktlint_issue import KtlintIssue
from lintro.parsers.ktlint.ktlint_parser import parse_ktlint_output

__all__ = ["KtlintIssue", "parse_ktlint_output"]

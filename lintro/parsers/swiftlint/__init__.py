"""SwiftLint parser package."""

from lintro.parsers.swiftlint.swiftlint_issue import SwiftlintIssue
from lintro.parsers.swiftlint.swiftlint_parser import parse_swiftlint_output

__all__ = ["SwiftlintIssue", "parse_swiftlint_output"]

"""golangci-lint parser package."""

from lintro.parsers.golangci_lint.golangci_lint_issue import GolangciLintIssue
from lintro.parsers.golangci_lint.golangci_lint_parser import (
    parse_golangci_lint_output,
)

__all__ = ["GolangciLintIssue", "parse_golangci_lint_output"]

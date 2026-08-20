"""Checkov parser module."""

from lintro.parsers.checkov.checkov_issue import CheckovIssue
from lintro.parsers.checkov.checkov_parser import parse_checkov_output

__all__ = ["CheckovIssue", "parse_checkov_output"]

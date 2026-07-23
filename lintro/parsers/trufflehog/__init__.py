"""TruffleHog parser module."""

from lintro.parsers.trufflehog.trufflehog_issue import TrufflehogIssue
from lintro.parsers.trufflehog.trufflehog_parser import parse_trufflehog_output

__all__ = ["TrufflehogIssue", "parse_trufflehog_output"]

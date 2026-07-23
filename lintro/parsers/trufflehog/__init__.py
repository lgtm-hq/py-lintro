"""TruffleHog parser module."""

from lintro.parsers.trufflehog.trufflehog_errors import (
    extract_trufflehog_scan_errors,
    is_benign_missing_path_error,
    scan_errors_are_all_benign,
)
from lintro.parsers.trufflehog.trufflehog_issue import TrufflehogIssue
from lintro.parsers.trufflehog.trufflehog_parser import parse_trufflehog_output

__all__ = [
    "TrufflehogIssue",
    "extract_trufflehog_scan_errors",
    "is_benign_missing_path_error",
    "parse_trufflehog_output",
    "scan_errors_are_all_benign",
]

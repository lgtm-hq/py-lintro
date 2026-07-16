"""SARIF ingestion parser module (proof of concept for issue #1066)."""

from lintro.parsers.sarif.sarif_issue import SarifIssue
from lintro.parsers.sarif.sarif_parser import parse_sarif_output

__all__ = ["SarifIssue", "parse_sarif_output"]

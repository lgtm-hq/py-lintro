"""OSV-Scanner parser module."""

from lintro.parsers.osv_scanner.osv_scanner_issue import OsvScannerIssue
from lintro.parsers.osv_scanner.osv_scanner_parser import (
    extract_osv_scanner_payload,
    parse_osv_scanner_output,
)
from lintro.parsers.osv_scanner.suppression_models import (
    ClassifiedSuppression,
    SuppressionEntry,
)
from lintro.parsers.osv_scanner.suppression_parser import (
    classify_suppressions,
    parse_suppressions,
)
from lintro.parsers.osv_scanner.suppression_status import SuppressionStatus

__all__ = [
    "ClassifiedSuppression",
    "OsvScannerIssue",
    "SuppressionEntry",
    "SuppressionStatus",
    "classify_suppressions",
    "extract_osv_scanner_payload",
    "parse_osv_scanner_output",
    "parse_suppressions",
]

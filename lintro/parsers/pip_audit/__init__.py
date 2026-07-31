"""pip-audit parser package.

Exports the issue type and parse function so imports match the layout
expected by ``skills/lintro-verify`` and the rest of the codebase.
"""

from lintro.parsers.pip_audit.pip_audit_issue import PipAuditIssue
from lintro.parsers.pip_audit.pip_audit_parser import (
    extract_pip_audit_payload,
    parse_pip_audit_output,
)

__all__ = [
    "PipAuditIssue",
    "extract_pip_audit_payload",
    "parse_pip_audit_output",
]

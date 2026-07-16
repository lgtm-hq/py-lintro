"""Base issue class for all linting tool issues.

This module provides a common base class for all issue types to reduce
duplication across the 14+ different issue dataclasses.

The base class includes:
- Common fields (file, line, column, message)
- A to_display_row() method for unified formatting with configurable field mapping
- A get_code() method for canonical rule-code access across formats
- A get_severity() method for normalized severity access
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from lintro.enums.severity_level import SeverityLevel, normalize_severity_level


def resolve_issue_code(issue: object) -> str:
    """Return the canonical rule code for an issue-like object.

    Prefers :meth:`BaseIssue.get_code` when available so ``DISPLAY_FIELD_MAP``
    aliases (``rule``, ``check_id``, ``test_id``, …) resolve consistently
    across grid, CSV, JSON, and other consumers. Falls back to a raw ``code``
    attribute for duck-typed objects that are not ``BaseIssue`` subclasses.

    Callables that return a non-string (for example ``MagicMock`` auto-attrs
    in unit tests) are ignored so the raw ``code`` attribute still wins.

    Args:
        issue: Issue-like object.

    Returns:
        Rule code string, or empty string when unavailable.
    """
    get_code = getattr(issue, "get_code", None)
    if callable(get_code):
        value = get_code()
        if isinstance(value, str):
            return value
        # Duck-typed callables may return None; BaseIssue.get_code() never does.
        if value is None:
            return ""
        if isinstance(value, (int, float)):
            return str(value)
        # Non-string return (e.g. MagicMock): fall through to raw attr.
    raw_code = getattr(issue, "code", "")
    if raw_code is None or raw_code == "":
        return ""
    return str(raw_code)


@dataclass
class BaseIssue:
    """Base class for all linting issues with unified display support.

    Provides common fields that are shared across all issue types.
    Specific issue types should inherit from this class and add their
    own fields as needed.

    The to_display_row() method converts the issue to a dictionary format
    suitable for the unified formatter. Subclasses can customize the mapping
    by setting DISPLAY_FIELD_MAP class variable instead of overriding the method.

    Attributes:
        DISPLAY_FIELD_MAP: Maps display keys to attribute names for custom fields.
            Default mappings: code->code, severity->severity, fixable->fixable.
            Example: {"severity": "level"} to map self.level to severity output.
        DEFAULT_SEVERITY: Fallback severity when the issue has no native value.
            Override in subclasses (e.g. INFO for pure-formatting tools).
        file: File path where the issue was found.
        line: Line number where the issue was found (1-based, 0 means unknown).
        column: Column number where the issue was found (1-based, 0 means unknown).
        message: Human-readable description of the issue.
        doc_url: URL to the rule's documentation page (empty when unavailable).
    """

    # Default field mapping - subclasses can override specific keys
    DISPLAY_FIELD_MAP: ClassVar[dict[str, str]] = {
        "code": "code",
        "severity": "severity",
        "fixable": "fixable",
        "message": "message",
    }

    DEFAULT_SEVERITY: ClassVar[SeverityLevel] = SeverityLevel.WARNING

    file: str = field(default="")
    line: int = field(default=0)
    column: int = field(default=0)
    message: str = field(default="")
    doc_url: str = field(default="", repr=False)

    def get_code(self) -> str:
        """Return the canonical rule/check code for this issue.

        Resolves the native identifier via ``DISPLAY_FIELD_MAP`` so tools that
        store it as ``rule``, ``check_id``, ``test_id``, ``advisory_id``, etc.
        still expose a consistent ``code`` value to formatters and writers.

        Returns:
            The rule code string, or empty string when unavailable.
        """
        attr_name = self.DISPLAY_FIELD_MAP.get("code", "code")
        raw = getattr(self, attr_name, None)
        if raw is None or raw == "":
            return ""
        return str(raw)

    def get_severity(self) -> SeverityLevel:
        """Return the normalized severity for this issue.

        Reads the native severity value via DISPLAY_FIELD_MAP (handles
        tools that store it as ``level``, ``issue_severity``, etc.),
        passes it through ``normalize_severity_level()``, and falls back
        to ``DEFAULT_SEVERITY`` when the value is empty/None.

        Returns:
            SeverityLevel: Normalized severity enum value.
        """
        attr_name = self.DISPLAY_FIELD_MAP.get("severity", "severity")
        raw = getattr(self, attr_name, None)

        if not raw:
            return self.DEFAULT_SEVERITY

        if isinstance(raw, SeverityLevel):
            return raw

        try:
            return normalize_severity_level(str(raw))
        except ValueError:
            return self.DEFAULT_SEVERITY

    def to_display_row(self) -> dict[str, str]:
        """Convert issue to unified display format.

        Returns a dictionary with standardized keys that the unified formatter
        can use to create consistent output across all tools.

        Uses DISPLAY_FIELD_MAP to resolve attribute names, allowing subclasses
        to customize field mapping without overriding this method.

        Returns:
            Dictionary with keys: file, line, column, code, message, severity,
            fixable, doc_url.
        """
        # Get the field mapping (supports inheritance)
        field_map = self.DISPLAY_FIELD_MAP

        # Resolve each mapped field
        fixable_attr = field_map.get("fixable", "fixable")
        message_attr = field_map.get("message", "message")

        code_val = self.get_code()
        fixable_val = getattr(self, fixable_attr, False)
        message_val = getattr(self, message_attr, "") or ""

        return {
            "file": self.file,
            "line": str(self.line) if self.line else "-",
            "column": str(self.column) if self.column else "-",
            "code": code_val,
            "message": message_val,
            "severity": str(self.get_severity()),
            "fixable": "Yes" if fixable_val else "",
            "doc_url": self.doc_url,
        }

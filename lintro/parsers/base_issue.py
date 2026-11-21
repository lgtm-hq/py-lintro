"""Base issue class for all linting tool issues.

This module provides a common base class for all issue types to reduce
duplication across the 14+ different issue dataclasses.

The base class includes:
- Common fields (file, line, column, message)
- A to_display_row() method for unified formatting with configurable field mapping
- A get_severity() method for normalized severity access
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import ClassVar

from lintro.enums.severity_level import SeverityLevel, normalize_severity_level

#: Separators a tool may put around lintro's guard. A backslash separates
#: path parts on Windows only; on POSIX it is an ordinary file-name
#: character, so ``a\.\-x.txt`` is one name and must be reported unchanged.
_SEPARATORS = "/\\" if os.sep == "\\" else "/"
#: ``lintro.utils.option_paths.SAFE_FIRST_CHARACTERS``; a test pins the copy.
_SAFE_FIRST = "_.+[({$"


def _guarded_name(rest: str) -> bool:
    r"""Return whether ``rest`` is a name lintro would have prefixed with ``./``.

    Mirrors ``lintro.utils.option_paths.starts_safely``; a test pins that the
    two agree. A parser cannot import it without a parsers/utils cycle.

    Args:
        rest: The text after a ``./`` (or ``.\\``) in a reported path.

    Returns:
        True when the first character is not a letter, a digit, a safe first
        character or a path separator.
    """
    first = rest[:1]
    return bool(first) and not (first.isalnum() or first in _SAFE_FIRST + _SEPARATORS)


def strip_option_guard(path: str) -> str:
    """Undo lintro's ``./`` guard on a path a tool reported back (#3584).

    Lintro hands ``@b.ts`` over as ``./@b.ts``. Tools report that as given
    (``./@b.ts``) or joined to their directory (``/proj/./@b.ts``). Only that
    ``./`` is removed; nothing else is normalised, so ``..`` and symlinks stay
    as the tool wrote them.

    Args:
        path: A path as a tool printed it.

    Returns:
        ``path`` without the ``./`` lintro added before a guarded name.
    """
    for sep in _SEPARATORS:
        guard = f".{sep}"
        if path.startswith(guard) and _guarded_name(path[2:]):
            return path[2:]
        index = path.find(f"{sep}{guard}")
        if index >= 0 and _guarded_name(path[index + 3 :]):
            return path[: index + 1] + path[index + 3 :]
    return path


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
        PREVIEW_FIXABLE: Include check findings in format previews when the tool
            lacks a per-issue fix signal. Does not claim public fixability.
        file: File path where the issue was found.
        line: Line number where the issue was found (1-based, 0 means unknown).
        column: Column number where the issue was found (1-based, 0 means unknown).
        message: Human-readable description of the issue.
        doc_url: URL to the rule's documentation page (empty when unavailable).
        category: Optional concern category (Security, Style, …) used by
            ``--group-by category``; empty until taxonomy enrichment.
        fixable: Whether the tool can automatically fix the issue.
    """

    # Default field mapping - subclasses can override specific keys
    DISPLAY_FIELD_MAP: ClassVar[dict[str, str]] = {
        "code": "code",
        "severity": "severity",
        "fixable": "fixable",
        "message": "message",
        "category": "category",
    }

    DEFAULT_SEVERITY: ClassVar[SeverityLevel] = SeverityLevel.WARNING
    PREVIEW_FIXABLE: ClassVar[bool] = False

    file: str = field(default="")
    line: int = field(default=0)
    column: int = field(default=0)
    message: str = field(default="")
    doc_url: str = field(default="", repr=False)
    category: str = field(default="")
    fixable: bool = field(default=False)

    def __setattr__(self, name: str, value: object) -> None:
        """Record ``file`` without the guard lintro added for the tool (#3584).

        Lintro hands a file named like an option to the tool as ``./-name``,
        and tools report that path back. Every issue, whether built by a
        parser or rewritten later, keeps the name the user sees: ``-name``.

        Args:
            name: Attribute being set.
            value: New value.
        """
        if name == "file" and isinstance(value, str):
            value = strip_option_guard(value)
        super().__setattr__(name, value)

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

    def get_code(self) -> str:
        """Return the canonical rule code for this issue.

        Resolves the native code attribute via ``DISPLAY_FIELD_MAP`` (handling
        tools that store it as ``rule``, ``check_id``, ``test_id``, etc.) so
        every formatter and writer renders the same identifier regardless of
        the underlying dataclass field name.

        Returns:
            The rule code as a string, or an empty string when unavailable.
        """
        attr_name = self.DISPLAY_FIELD_MAP.get("code", "code")
        raw = getattr(self, attr_name, None)
        return str(raw) if raw else ""

    def to_display_row(self) -> dict[str, str]:
        """Convert issue to unified display format.

        Returns a dictionary with standardized keys that the unified formatter
        can use to create consistent output across all tools.

        Uses DISPLAY_FIELD_MAP to resolve attribute names, allowing subclasses
        to customize field mapping without overriding this method.

        Returns:
            Dictionary with keys: file, line, column, code, message, severity,
            fixable, doc_url, category.
        """
        # Get the field mapping (supports inheritance)
        field_map = self.DISPLAY_FIELD_MAP

        # Resolve each mapped field
        fixable_attr = field_map.get("fixable", "fixable")
        message_attr = field_map.get("message", "message")
        category_attr = field_map.get("category", "category")

        fixable_val = getattr(self, fixable_attr, False)
        message_val = getattr(self, message_attr, "") or ""
        category_val = getattr(self, category_attr, None) or ""

        return {
            "file": self.file,
            "line": str(self.line) if self.line else "-",
            "column": str(self.column) if self.column else "-",
            "code": self.get_code(),
            "message": message_val,
            "severity": str(self.get_severity()),
            "fixable": "Yes" if fixable_val else "",
            "doc_url": self.doc_url,
            "category": str(category_val) if category_val else "",
        }

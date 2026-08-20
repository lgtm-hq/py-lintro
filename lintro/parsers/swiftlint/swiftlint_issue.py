"""Models for SwiftLint issues."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from lintro.parsers.base_issue import BaseIssue


@dataclass
class SwiftlintIssue(BaseIssue):
    """Represents a SwiftLint linting issue.

    SwiftLint emits issues as a JSON array (``--reporter json``) where each
    element has the shape::

        {
          "file": "/abs/path/Sample.swift",
          "line": 4,
          "character": 9,
          "severity": "Error",
          "type": "Identifier Name",
          "rule_id": "identifier_name",
          "reason": "Variable name 'x' should be between 3 and 40 characters long"
        }

    Attributes:
        DISPLAY_FIELD_MAP: Mapping of display field names to attribute names.
        code: SwiftLint rule identifier (e.g., ``identifier_name``).
        level: Severity level as reported by SwiftLint (``Warning``/``Error``).
        rule_type: Human-readable rule category (e.g., ``Identifier Name``).
    """

    DISPLAY_FIELD_MAP: ClassVar[dict[str, str]] = {
        **BaseIssue.DISPLAY_FIELD_MAP,
        "severity": "level",
    }

    code: str = field(default="")
    level: str | None = field(default=None)
    rule_type: str | None = field(default=None)

"""djLint issue model.

This module defines the dataclass used to represent djLint findings in a
normalized form that Lintro formatters can consume. djLint operates in two
modes that Lintro surfaces through the same issue type:

- Formatting (``--check`` / ``--reformat``): whole-file reformat diffs that
  ``fix`` can apply automatically (``fixable=True``, no ``code``).
- Linting (``--lint``): rule-based findings such as ``H013`` that carry a
  code and cannot be auto-fixed (``fixable=False``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from lintro.enums.severity_level import SeverityLevel
from lintro.parsers.base_issue import BaseIssue


@dataclass
class DjlintIssue(BaseIssue):
    """Represents a single djLint finding parsed from CLI output.

    Attributes:
        DEFAULT_SEVERITY: Defaults to WARNING for rule findings; formatting
            diffs are informational reformat suggestions.
        DISPLAY_FIELD_MAP: Mapping of display field names to attribute names.
        code: Rule code (e.g., "H013"), empty for formatting diffs.
        fixable: Whether ``djlint --reformat`` can auto-fix this finding.
            True for formatting diffs, False for rule-based lint findings.
    """

    DEFAULT_SEVERITY: ClassVar[SeverityLevel] = SeverityLevel.WARNING

    DISPLAY_FIELD_MAP: ClassVar[dict[str, str]] = {
        **BaseIssue.DISPLAY_FIELD_MAP,
        "code": "code",
    }

    code: str = field(default="")
    fixable: bool = field(default=True)

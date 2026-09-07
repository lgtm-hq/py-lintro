"""Import-linter issue model.

This module defines the dataclass representing a single broken import chain
reported by ``lint-imports`` (import-linter).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from lintro.enums.severity_level import SeverityLevel
from lintro.parsers.base_issue import BaseIssue


@dataclass
class ImportLinterIssue(BaseIssue):
    """Represents a broken import chain found by import-linter.

    import-linter checks architectural contracts rather than individual
    source lines, so ``file`` carries the importing module's dotted path and
    ``line`` is always ``0``.

    Attributes:
        DEFAULT_SEVERITY: Defaults to ERROR (a broken contract fails the run).
        code: Name of the broken contract (e.g. ``"Layered architecture"``).
    """

    DEFAULT_SEVERITY: ClassVar[SeverityLevel] = SeverityLevel.ERROR

    code: str = field(default="")

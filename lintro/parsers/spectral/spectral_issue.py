"""Typed structure representing a single Spectral diagnostic."""

from dataclasses import dataclass, field
from typing import ClassVar

from lintro.enums.severity_level import SeverityLevel
from lintro.parsers.base_issue import BaseIssue


@dataclass
class SpectralIssue(BaseIssue):
    """Container for a single Spectral finding.

    Spectral is a JSON/YAML linter for OpenAPI, AsyncAPI, and JSON Schema
    documents. Its native JSON output carries a JSON path pointing at the
    offending node within the API document, which is retained here because it
    is the most precise location signal for structured specs.

    Attributes:
        DEFAULT_SEVERITY: Defaults to WARNING (most built-in rules warn).
        code: Rule code that was violated (e.g., ``oas3-api-servers``).
        severity: Normalized severity string
            (``error``/``warning``/``info``/``hint``).
        path: Dotted JSON path to the offending node
            (e.g., ``paths./users.get``); empty for document-level findings.
    """

    DEFAULT_SEVERITY: ClassVar[SeverityLevel] = SeverityLevel.WARNING

    code: str = field(default="")
    severity: str = field(default="warning")
    path: str = field(default="")

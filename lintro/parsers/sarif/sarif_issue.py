"""SARIF issue model (proof of concept for issue #1066).

This is an evaluation artifact, not a production parser. It models a single
finding decoded from a SARIF 2.1.0 ``result`` object into lintro's shared
``BaseIssue`` shape so the SARIF-to-issue mapping can be validated in tests
without wiring SARIF into any tool plugin.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from lintro.parsers.base_issue import BaseIssue


@dataclass
class SarifIssue(BaseIssue):
    """A single finding decoded from a SARIF 2.1.0 ``result`` object.

    Attributes:
        DISPLAY_FIELD_MAP: Maps display keys to attribute names. SARIF stores
            the rule identifier in ``ruleId`` and the severity in ``level``,
            which are surfaced here as ``code`` and ``severity`` respectively.
        code: Rule identifier from ``result.ruleId`` (e.g. ``F401``).
        level: SARIF severity level (``error``, ``warning``, ``note``,
            ``none``); normalized to a ``SeverityLevel`` by ``get_severity()``.
        fixable: True when the ``result`` carries one or more ``fixes``.
        end_line: End line of the primary region, when present.
        end_column: End column of the primary region, when present.
        tool_name: The ``driver.name`` of the run that produced this result.
    """

    DISPLAY_FIELD_MAP: ClassVar[dict[str, str]] = {
        **BaseIssue.DISPLAY_FIELD_MAP,
        "code": "code",
        "severity": "level",
    }

    code: str = field(default="")
    level: str = field(default="")
    fixable: bool = field(default=False)
    end_line: int | None = field(default=None)
    end_column: int | None = field(default=None)
    tool_name: str = field(default="")

"""Typed structure representing a single Stylelint diagnostic."""

from dataclasses import dataclass, field

from lintro.parsers.base_issue import BaseIssue


@dataclass
class StylelintIssue(BaseIssue):
    """Simple container for Stylelint findings.

    Attributes:
        code: Rule name that was violated (e.g., ``color-hex-length``). Syntax
            errors use the ``CssSyntaxError`` pseudo-rule.
        severity: Native severity as reported by stylelint ('error', 'warning').
        fixable: Whether the issue is auto-fixable. Stylelint's JSON formatter
            does not expose per-warning fix metadata, so this defaults to False.
    """

    code: str = field(default="")
    severity: str = field(default="error")
    fixable: bool = field(default=False)

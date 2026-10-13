"""Terraform issue model.

This module defines the TerraformIssue dataclass for representing issues
found by ``terraform fmt`` (formatting) and ``terraform validate`` (linting).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from lintro.parsers.base_issue import BaseIssue


@dataclass
class TerraformIssue(BaseIssue):
    """Represents an issue found by Terraform.

    Attributes:
        DISPLAY_FIELD_MAP: Mapping of display field names to attribute names.
        level: Severity level (error, warning).
        code: Origin of the issue (``fmt`` for formatting, ``validate`` for
            configuration validation, ``init`` for initialization failures).
    """

    DISPLAY_FIELD_MAP: ClassVar[dict[str, str]] = {
        **BaseIssue.DISPLAY_FIELD_MAP,
        "severity": "level",
    }

    level: str = field(default="error")
    code: str = field(default="")

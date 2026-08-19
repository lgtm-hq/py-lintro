"""Issue concern-category definitions for grouping and taxonomy."""

from __future__ import annotations

from enum import StrEnum


class IssueCategory(StrEnum):
    """High-level concern categories for lint and security findings.

    Values are title-case display labels used as section headers when
    ``--group-by category`` is active.
    """

    SECURITY = "Security"
    CORRECTNESS = "Correctness"
    PERFORMANCE = "Performance"
    STYLE = "Style"
    ACCESSIBILITY = "Accessibility"
    DOCUMENTATION = "Documentation"
    INFRASTRUCTURE = "Infrastructure"


# Stable display order for category sections.
ISSUE_CATEGORY_ORDER: tuple[IssueCategory, ...] = (
    IssueCategory.SECURITY,
    IssueCategory.CORRECTNESS,
    IssueCategory.PERFORMANCE,
    IssueCategory.STYLE,
    IssueCategory.ACCESSIBILITY,
    IssueCategory.DOCUMENTATION,
    IssueCategory.INFRASTRUCTURE,
)

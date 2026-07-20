"""Taxonomy helpers that map issues to concern categories.

Resolution order:
1. Existing non-empty ``issue.category`` (normalized when it matches a known label)
2. Cheap rule-code heuristics (e.g. oxlint ``jsx-a11y`` → Accessibility)
3. Known tool-name defaults from the issue taxonomy
4. ``ToolType`` fallback from the tool registry
5. ``Correctness`` as the final default
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from lintro.enums.issue_category import IssueCategory
from lintro.enums.tool_type import ToolType

if TYPE_CHECKING:
    from lintro.parsers.base_issue import BaseIssue

# Tool-name defaults aligned with the category taxonomy in issue #616.
_TOOL_NAME_CATEGORIES: dict[str, IssueCategory] = {
    "bandit": IssueCategory.SECURITY,
    "gitleaks": IssueCategory.SECURITY,
    "semgrep": IssueCategory.SECURITY,
    "cargo_audit": IssueCategory.SECURITY,
    "cargo-audit": IssueCategory.SECURITY,
    "cargo_deny": IssueCategory.SECURITY,
    "cargo-deny": IssueCategory.SECURITY,
    "pip_audit": IssueCategory.SECURITY,
    "pip-audit": IssueCategory.SECURITY,
    "osv_scanner": IssueCategory.SECURITY,
    "osv-scanner": IssueCategory.SECURITY,
    "mypy": IssueCategory.CORRECTNESS,
    "tsc": IssueCategory.CORRECTNESS,
    "vue_tsc": IssueCategory.CORRECTNESS,
    "vue-tsc": IssueCategory.CORRECTNESS,
    "svelte_check": IssueCategory.CORRECTNESS,
    "svelte-check": IssueCategory.CORRECTNESS,
    "astro_check": IssueCategory.CORRECTNESS,
    "astro-check": IssueCategory.CORRECTNESS,
    "clippy": IssueCategory.CORRECTNESS,
    "black": IssueCategory.STYLE,
    "prettier": IssueCategory.STYLE,
    "oxfmt": IssueCategory.STYLE,
    "rustfmt": IssueCategory.STYLE,
    "shfmt": IssueCategory.STYLE,
    "taplo": IssueCategory.STYLE,
    "pydoclint": IssueCategory.DOCUMENTATION,
    "markdownlint": IssueCategory.DOCUMENTATION,
    "vale": IssueCategory.DOCUMENTATION,
    "hadolint": IssueCategory.INFRASTRUCTURE,
    "actionlint": IssueCategory.INFRASTRUCTURE,
    "shellcheck": IssueCategory.INFRASTRUCTURE,
}

_CATEGORY_ALIASES: dict[str, IssueCategory] = {
    cat.value.lower(): cat for cat in IssueCategory
}
_CATEGORY_ALIASES.update(
    {
        "security": IssueCategory.SECURITY,
        "correctness": IssueCategory.CORRECTNESS,
        "performance": IssueCategory.PERFORMANCE,
        "perf": IssueCategory.PERFORMANCE,
        "style": IssueCategory.STYLE,
        "formatting": IssueCategory.STYLE,
        "accessibility": IssueCategory.ACCESSIBILITY,
        "a11y": IssueCategory.ACCESSIBILITY,
        "documentation": IssueCategory.DOCUMENTATION,
        "docs": IssueCategory.DOCUMENTATION,
        "infrastructure": IssueCategory.INFRASTRUCTURE,
        "infra": IssueCategory.INFRASTRUCTURE,
    },
)

# Ruff/flake8-style performance and complexity codes.
_RUFF_PERFORMANCE_PREFIXES: tuple[str, ...] = (
    "PERF",
    "C90",
    "PLR091",  # too-many-* complexity rules
)

_A11Y_PATTERN = re.compile(
    r"(?:^|[/(._-])(?:jsx-a11y|a11y|eslint-plugin-jsx-a11y)(?:$|[/)._-])",
    re.IGNORECASE,
)
_PERF_PATTERN = re.compile(
    r"(?:^|[/(._-])(?:perf|performance)(?:$|[/)._-])",
    re.IGNORECASE,
)
_SECURITY_PATTERN = re.compile(
    r"(?:^|[/(._-])(?:security|sec|snyk|bandit)(?:$|[/)._-])",
    re.IGNORECASE,
)


def normalize_issue_category(value: str | IssueCategory | None) -> IssueCategory | None:
    """Normalize a raw category label to ``IssueCategory`` when recognized.

    Args:
        value: Raw category string, enum, or empty/None.

    Returns:
        Matching ``IssueCategory``, or ``None`` when unrecognized/empty.
    """
    if isinstance(value, IssueCategory):
        return value
    if not value:
        return None
    return _CATEGORY_ALIASES.get(str(value).strip().lower())


def _issue_code(issue: BaseIssue) -> str:
    """Return the display rule code for an issue.

    Args:
        issue: Issue to inspect.

    Returns:
        Rule/code string, or empty when unavailable.
    """
    field_map = getattr(issue, "DISPLAY_FIELD_MAP", {}) or {}
    code_attr = field_map.get("code", "code")
    return str(getattr(issue, code_attr, None) or getattr(issue, "code", "") or "")


def category_from_rule_code(code: str) -> IssueCategory | None:
    """Map a rule code to a category using cheap heuristics.

    Args:
        code: Tool rule identifier (e.g. ``jsx-a11y/alt-text``, ``PERF401``).

    Returns:
        Matched category, or ``None`` when no heuristic applies.
    """
    if not code:
        return None
    normalized = code.strip()
    upper = normalized.upper()

    if _A11Y_PATTERN.search(normalized):
        return IssueCategory.ACCESSIBILITY

    if upper.startswith(_RUFF_PERFORMANCE_PREFIXES) or _PERF_PATTERN.search(normalized):
        return IssueCategory.PERFORMANCE

    if _SECURITY_PATTERN.search(normalized):
        return IssueCategory.SECURITY

    return None


def category_from_tool_type(tool_type: ToolType) -> IssueCategory:
    """Derive a default category from a tool's ``ToolType`` flags.

    Args:
        tool_type: Tool capability flags.

    Returns:
        Best-fit category for the tool type.
    """
    if tool_type & ToolType.SECURITY:
        return IssueCategory.SECURITY
    if tool_type & ToolType.TYPE_CHECKER:
        return IssueCategory.CORRECTNESS
    if tool_type & ToolType.DOCUMENTATION:
        return IssueCategory.DOCUMENTATION
    if tool_type & ToolType.INFRASTRUCTURE:
        return IssueCategory.INFRASTRUCTURE
    if tool_type & ToolType.FORMATTER and not (tool_type & ToolType.LINTER):
        return IssueCategory.STYLE
    if tool_type & ToolType.FORMATTER:
        return IssueCategory.STYLE
    if tool_type & ToolType.LINTER:
        return IssueCategory.CORRECTNESS
    return IssueCategory.CORRECTNESS


def _tool_type_for_name(tool_name: str | None) -> ToolType | None:
    """Look up a tool's ``ToolType`` from the registry when available.

    Args:
        tool_name: Tool identifier.

    Returns:
        Tool type flags, or ``None`` when the tool is unknown/unavailable.
    """
    if not tool_name:
        return None
    try:
        from lintro.plugins.registry import ToolRegistry

        name = tool_name.lower().replace("-", "_")
        for candidate in (tool_name.lower(), name, tool_name.lower().replace("_", "-")):
            if ToolRegistry.is_registered(candidate):
                return ToolRegistry.get(candidate).definition.tool_type
    except (ImportError, AttributeError, KeyError, RuntimeError, TypeError, ValueError):
        return None
    return None


def resolve_issue_category(
    issue: BaseIssue,
    *,
    tool_name: str | None = None,
) -> IssueCategory:
    """Resolve the concern category for a single issue.

    Args:
        issue: Issue to categorize.
        tool_name: Optional tool that produced the issue.

    Returns:
        Resolved ``IssueCategory``.
    """
    existing = normalize_issue_category(getattr(issue, "category", None))
    if existing is not None:
        return existing

    from_code = category_from_rule_code(_issue_code(issue))
    if from_code is not None:
        return from_code

    if tool_name:
        tool_key = tool_name.lower()
        if tool_key in _TOOL_NAME_CATEGORIES:
            return _TOOL_NAME_CATEGORIES[tool_key]
        underscore = tool_key.replace("-", "_")
        if underscore in _TOOL_NAME_CATEGORIES:
            return _TOOL_NAME_CATEGORIES[underscore]

    tool_type = _tool_type_for_name(tool_name)
    if tool_type is not None:
        return category_from_tool_type(tool_type)

    return IssueCategory.CORRECTNESS


def enrich_issue_category(
    issue: BaseIssue,
    *,
    tool_name: str | None = None,
) -> IssueCategory:
    """Resolve and persist ``issue.category`` when empty.

    Args:
        issue: Issue to enrich.
        tool_name: Optional tool that produced the issue.

    Returns:
        Category assigned to the issue.
    """
    category = resolve_issue_category(issue, tool_name=tool_name)
    current = getattr(issue, "category", "") or ""
    if not current:
        issue.category = category.value
    else:
        normalized = normalize_issue_category(current)
        if normalized is not None:
            issue.category = normalized.value
    return category


def enrich_issues_with_categories(
    issues: list[BaseIssue] | None,
    *,
    tool_name: str | None = None,
) -> None:
    """Enrich a list of issues with resolved categories in place.

    Args:
        issues: Issues to enrich (ignored when None/empty).
        tool_name: Optional tool that produced the issues.
    """
    if not issues:
        return
    for issue in issues:
        enrich_issue_category(issue, tool_name=tool_name)

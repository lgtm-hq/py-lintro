"""Canonical tool name definitions.

Provides a stable set of identifiers for tools used across the codebase.
"""

from __future__ import annotations

from enum import StrEnum, auto


class ToolName(StrEnum):
    """Supported tool identifiers in lower-case values."""

    ACTIONLINT = auto()
    ASTRO_CHECK = auto()
    BANDIT = auto()
    BLACK = auto()
    CARGO_AUDIT = auto()
    CARGO_DENY = auto()
    CLIPPY = auto()
    DOTENV_LINTER = auto()
    GITLEAKS = auto()
    HADOLINT = auto()
    IDIOM_REVIEW = auto()
    MARKDOWNLINT = auto()
    MYPY = auto()
    OSV_SCANNER = auto()
    OXFMT = auto()
    OXLINT = auto()
    PRETTIER = auto()
    PYDOCLINT = auto()
    PYTEST = auto()
    RUFF = auto()
    RUSTC = auto()
    RUSTFMT = auto()
    SEMGREP = auto()
    SHELLCHECK = auto()
    SHFMT = auto()
    SQLFLUFF = auto()
    SVELTE_CHECK = auto()
    TAPLO = auto()
    TSC = auto()
    VALE = auto()
    VUE_TSC = auto()
    YAMLLINT = auto()


def normalize_tool_name(value: str | ToolName) -> ToolName:
    """Normalize a raw name to ToolName.

    Args:
        value: Tool name as str or ToolName.

    Returns:
        ToolName: Normalized enum member.

    Raises:
        ValueError: If the value is not a valid tool name.
    """
    if isinstance(value, ToolName):
        return value
    # Normalize hyphens to underscores (e.g., "astro-check" -> "astro_check")
    normalized = value.strip().replace("-", "_")
    try:
        return ToolName[normalized.upper()]
    except KeyError as err:
        raise ValueError(
            f"Unknown tool name: {value!r}. Supported tools: {list(ToolName)}",
        ) from err

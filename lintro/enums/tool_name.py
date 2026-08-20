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
    COMMITLINT = auto()
    DOTENV_LINTER = auto()
    GITLEAKS = auto()
    GOLANGCI_LINT = auto()
    HADOLINT = auto()
    HTML_VALIDATE = auto()
    IDIOM_REVIEW = auto()
    MARKDOWNLINT = auto()
    MYPY = auto()
    OSV_SCANNER = auto()
    OXFMT = auto()
    OXLINT = auto()
    PIP_AUDIT = auto()
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
    STYLELINT = auto()
    SVELTE_CHECK = auto()
    TAPLO = auto()
    TRUFFLEHOG = auto()
    TSC = auto()
    VALE = auto()
    VUE_TSC = auto()
    YAMLLINT = auto()


def tool_name_aliases(name: str) -> tuple[str, ...]:
    """Return hyphen and underscore spellings for a tool name.

    Registry keys mix the two forms (``astro-check`` vs ``astro_check``).
    Callers that look up tools or snapshots should try every alias.

    Args:
        name: Raw tool name (any spelling or case).

    Returns:
        Deduplicated lowercase candidates, requested spelling first.
    """
    lowered = name.strip().lower()
    aliases: list[str] = [lowered]
    underscored = lowered.replace("-", "_")
    hyphenated = lowered.replace("_", "-")
    if underscored not in aliases:
        aliases.append(underscored)
    if hyphenated not in aliases:
        aliases.append(hyphenated)
    return tuple(aliases)


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

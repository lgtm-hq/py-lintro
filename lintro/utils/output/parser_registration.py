"""Register all parsers at module load time.

This module registers all tool parsers with the ParserRegistry,
providing O(1) lookup for parser dispatch and fixability predicates.
"""

# mypy: ignore-errors
# Note: mypy errors are suppressed because lintro runs mypy from file's directory,
# breaking package resolution. When run properly (mypy lintro/...), this file passes.

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from lintro.enums.tool_name import ToolName
from lintro.parsers.actionlint.actionlint_parser import parse_actionlint_output
from lintro.parsers.astro_check.astro_check_parser import parse_astro_check_output
from lintro.parsers.bandit.bandit_parser import parse_bandit_output
from lintro.parsers.black.black_issue import BlackIssue
from lintro.parsers.black.black_parser import parse_black_output
from lintro.parsers.clippy.clippy_parser import parse_clippy_output
from lintro.parsers.hadolint.hadolint_parser import parse_hadolint_output
from lintro.parsers.markdownlint.markdownlint_parser import parse_markdownlint_output
from lintro.parsers.mypy.mypy_parser import parse_mypy_output
from lintro.parsers.ruff.ruff_format_issue import RuffFormatIssue
from lintro.parsers.ruff.ruff_issue import RuffIssue
from lintro.parsers.ruff.ruff_parser import parse_ruff_output
from lintro.parsers.svelte_check.svelte_check_parser import parse_svelte_check_output
from lintro.parsers.vue_tsc.vue_tsc_parser import parse_vue_tsc_output
from lintro.parsers.yamllint.yamllint_parser import parse_yamllint_output
from lintro.utils.output.parser_registry import ParserRegistry

# -----------------------------------------------------------------------------
# Fixability Predicates
# -----------------------------------------------------------------------------


def _ruff_is_fixable(issue: object) -> bool:
    """Check if a Ruff issue is fixable.

    Args:
        issue: The issue object to check.

    Returns:
        True if the issue is fixable (format issue or has fixable attribute).
    """
    return isinstance(issue, RuffFormatIssue) or (
        isinstance(issue, RuffIssue) and getattr(issue, "fixable", False)
    )


def _black_is_fixable(issue: object) -> bool:
    """Check if a Black issue is fixable.

    Args:
        issue: The issue object to check.

    Returns:
        True if the issue is a BlackIssue (all Black issues are fixable).
    """
    return isinstance(issue, BlackIssue) and getattr(issue, "fixable", True)


# -----------------------------------------------------------------------------
# Special Parsers
# -----------------------------------------------------------------------------


class ParserError(Exception):
    """Exception raised when parsing tool output fails.

    This exception is raised instead of silently returning empty results,
    allowing callers to distinguish between "no issues found" and "parsing failed".
    """


# Informational, non-JSON messages that Lintro's bandit wrapper may surface for
# a successful, zero-issue run. These are not diagnostic output and must not be
# treated as parse failures (#1534). Real unparseable bandit output (e.g. from
# an actual bandit run) is still surfaced as an error to preserve #1044.
_BANDIT_INFORMATIONAL_SENTINELS: tuple[str, ...] = (
    "No .py/.pyi files found",
    "Bandit ran successfully and found no issues",
)


def _parse_bandit_output(output: str) -> list[Any]:
    """Parse Bandit output, handling JSON format.

    Args:
        output: Raw Bandit output (expected to be JSON).

    Returns:
        List of parsed Bandit issues.

    Raises:
        ParserError: If the output cannot be parsed as valid JSON.
    """
    # Treat known informational, non-JSON sentinels as "no issues" rather than a
    # parse failure so a clean pass never emits parse-error noise (#1534).
    if any(sentinel in output for sentinel in _BANDIT_INFORMATIONAL_SENTINELS):
        return []

    try:
        return parse_bandit_output(bandit_data=json.loads(output))
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
        logger.error(f"Failed to parse Bandit output: {e}")
        raise ParserError(f"Failed to parse Bandit output: {e}") from e


# -----------------------------------------------------------------------------
# Registration
# -----------------------------------------------------------------------------


def register_all_parsers() -> None:
    """Register all tool parsers with the registry.

    This function should be called once during application initialization
    to populate the ParserRegistry with all available tool parsers.
    """
    # Ruff - Python linter/formatter
    ParserRegistry.register(
        ToolName.RUFF.value,
        parse_ruff_output,
        is_fixable=_ruff_is_fixable,
    )

    # Black - Python formatter
    ParserRegistry.register(
        ToolName.BLACK.value,
        parse_black_output,
        is_fixable=_black_is_fixable,
    )

    # Mypy - Python type checker
    ParserRegistry.register(
        ToolName.MYPY.value,
        parse_mypy_output,
    )

    # Actionlint - GitHub Actions linter
    ParserRegistry.register(
        ToolName.ACTIONLINT.value,
        parse_actionlint_output,
    )

    # Hadolint - Dockerfile linter
    ParserRegistry.register(
        ToolName.HADOLINT.value,
        parse_hadolint_output,
    )

    # Yamllint - YAML linter
    ParserRegistry.register(
        ToolName.YAMLLINT.value,
        parse_yamllint_output,
    )

    # Markdownlint - Markdown linter
    ParserRegistry.register(
        ToolName.MARKDOWNLINT.value,
        parse_markdownlint_output,
    )

    # Bandit - Python security linter (special JSON handling)
    ParserRegistry.register(
        ToolName.BANDIT.value,
        _parse_bandit_output,
    )

    # Clippy - Rust linter
    ParserRegistry.register(
        ToolName.CLIPPY.value,
        parse_clippy_output,
    )

    # Astro check - Astro type checker
    ParserRegistry.register(
        ToolName.ASTRO_CHECK.value,
        parse_astro_check_output,
    )

    # Svelte-check - Svelte type checker
    ParserRegistry.register(
        ToolName.SVELTE_CHECK.value,
        parse_svelte_check_output,
    )

    # Vue-tsc - Vue TypeScript type checker
    ParserRegistry.register(
        ToolName.VUE_TSC.value,
        parse_vue_tsc_output,
    )


# Auto-register parsers when module is imported
register_all_parsers()

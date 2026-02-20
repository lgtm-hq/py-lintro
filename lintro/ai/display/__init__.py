"""AI output rendering package.

Supports three environments:
- Terminal: Rich Panels with section headers
- GitHub Actions: ``::group::`` / ``::endgroup::`` collapsible sections
- Markdown: ``<details><summary>`` collapsible sections
- JSON: Full data always included (handled separately in json_output)

Used by both ``chk`` (summaries, explanations) and ``fmt`` (fix suggestions).
"""

from lintro.ai.display.fixes import (
    render_fixes,
    render_fixes_github,
    render_fixes_markdown,
    render_fixes_terminal,
)
from lintro.ai.display.shared import (
    LEADING_NUMBER_RE,
    cost_str,
    is_github_actions,
    print_code_panel,
    print_section_header,
)
from lintro.ai.display.summary import (
    render_summary,
    render_summary_github,
    render_summary_markdown,
    render_summary_terminal,
)
from lintro.ai.display.validation import render_validation, render_validation_terminal

# Backward-compatible aliases for private names used by other modules.
_LEADING_NUMBER_RE = LEADING_NUMBER_RE
_is_github_actions = is_github_actions
_cost_str = cost_str
_print_section_header = print_section_header
_print_code_panel = print_code_panel

__all__ = [
    # Shared helpers (public names)
    "LEADING_NUMBER_RE",
    "cost_str",
    "is_github_actions",
    "print_code_panel",
    "print_section_header",
    # Fix rendering
    "render_fixes",
    "render_fixes_github",
    "render_fixes_markdown",
    "render_fixes_terminal",
    # Summary rendering
    "render_summary",
    "render_summary_github",
    "render_summary_markdown",
    "render_summary_terminal",
    # Validation rendering
    "render_validation",
    "render_validation_terminal",
    # Backward-compatible aliases
    "_LEADING_NUMBER_RE",
    "_cost_str",
    "_is_github_actions",
    "_print_code_panel",
    "_print_section_header",
]

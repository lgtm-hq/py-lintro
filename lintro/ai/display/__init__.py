"""AI output rendering package.

Supports the following environments:
- Terminal: Rich Panels with section headers
- GitHub Actions: ``::group::`` / ``::endgroup::`` collapsible sections
- Markdown: ``<details><summary>`` collapsible sections
- JSON: Full data always included (handled separately in json_output)

Used by both ``chk`` (summaries, explanations) and ``fmt`` (fix suggestions).
"""

from lintro.ai.display.shared import (
    LEADING_NUMBER_RE,
    cost_str,
    is_github_actions,
    print_code_panel,
    print_section_header,
)
from lintro.ai.display.summary import (
    render_summary,
    render_summary_annotations,
    render_summary_github,
    render_summary_markdown,
    render_summary_terminal,
)

__all__ = [
    "LEADING_NUMBER_RE",
    "cost_str",
    "is_github_actions",
    "print_code_panel",
    "print_section_header",
    "render_summary",
    "render_summary_annotations",
    "render_summary_github",
    "render_summary_markdown",
    "render_summary_terminal",
]

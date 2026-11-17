"""Format lint results as the review prompt's digest (#2571, #3497).

The digest gives the model the deterministic linters' findings, and names
every tool that did not run, did not complete, or is unavailable, so its
silence never reads as a clean result.
"""

from __future__ import annotations

from html import escape

from lintro.ai.prompts.review import format_lint_results_section
from lintro.ai.review.lint_report_entry import REPORT_NOTE_METADATA_KEY
from lintro.models.core.tool_result import ToolResult
from lintro.utils.result_reason import incomplete_reason, unavailable_reason

__all__ = ["format_lint_results_for_prompt"]


def format_lint_results_for_prompt(
    *,
    results: list[ToolResult],
    max_entries: int = 200,
) -> str:
    """Format lint tool results as a compact prompt digest.

    A tool that did not run or did not complete gets one line naming why,
    ahead of the findings, so its silence never reads as a clean result
    (#3497); an unavailable tool gets one too. Findings it did report are
    kept like any other. Both lists are capped at ``max_entries``.

    Every piece of tool-supplied text is untrusted, so ``&``, ``<`` and ``>``
    are escaped and none of it can open or close prompt markup. The random
    boundary fence the prompt builder puts around this section is the real
    protection; the escaping keeps the section itself well formed.

    Args:
        results: Tool results from ``run_lint_on_changed_files``.
        max_entries: Maximum number of issue entries, and of tool notes, to
            include.

    Returns:
        Lint digest wrapped in ``<lint_results>`` tags, or empty string.
    """
    notes = [
        f"Tool: {_text(result.name)} | {_text(note)}"
        for result in results
        if (note := _tool_note(result))
    ]
    if len(notes) > max_entries:
        notes = [
            *notes[:max_entries],
            f"(truncated: {len(notes) - max_entries} more tool notes)",
        ]
    lines: list[str] = []
    for result in results:
        if not result.issues:
            continue
        for issue in result.issues:
            if len(lines) >= max_entries:
                break
            code = getattr(issue, "code", "") or "unknown"
            message = getattr(issue, "message", "") or ""
            file_path = getattr(issue, "file", "") or ""
            line_no = getattr(issue, "line", None)
            line_suffix = f" | line: {line_no}" if line_no else ""
            lines.append(
                f"Tool: {_text(result.name)} | file: {_text(file_path)}"
                f"{line_suffix}\n"
                f"Code: {_text(code)} | Message: {_text(message)}",
            )
        if len(lines) >= max_entries:
            break

    if not lines and not notes:
        return ""

    digest = "\n\n".join([*notes, *lines])
    if len(lines) >= max_entries:
        digest += "\n\n(truncated lint digest)"
    return format_lint_results_section(digest=digest)


def _tool_note(result: ToolResult) -> str | None:
    """Name a tool that did not run, did not complete, or is unavailable.

    Args:
        result: One tool's result.

    Returns:
        The note for the digest, or None for a complete or skipped tool.
    """
    reason = incomplete_reason(result)
    if reason:
        return f"did not run / incomplete: {reason}"
    note = result.metadata.get(REPORT_NOTE_METADATA_KEY)
    return unavailable_reason(result) or (note if isinstance(note, str) else None)


def _text(value: object) -> str:
    """Render untrusted tool text with ``&``, ``<`` and ``>`` escaped.

    Args:
        value: Text, or any value, from a tool result.

    Returns:
        The escaped text.
    """
    return escape(str(value), quote=False)

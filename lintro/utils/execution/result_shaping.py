"""Post-processing applied to raw tool results before aggregation.

These helpers are pure transformations over :class:`ToolResult` values: doc-URL
enrichment, remaining-count fallbacks, and the dry-run filter that reduces a
check-mode result to the subset a real ``fmt`` run would actually fix. Keeping
them out of the executor lets the execute phase read as orchestration only
(issue #1823).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from lintro.models.core.tool_result import ToolResult
    from lintro.parsers.base_issue import BaseIssue
    from lintro.plugins.base import BaseToolPlugin


def get_remaining_count(result: ToolResult) -> int:
    """Get the remaining issue count from a :class:`ToolResult`.

    Falls back to ``issues_count`` when ``remaining_issues_count`` is not set,
    then to 0 if neither is available.

    Args:
        result: The tool result to inspect.

    Returns:
        int: Number of remaining issues.
    """
    if result.remaining_issues_count is not None:
        return result.remaining_issues_count
    if result.issues_count is not None:
        return result.issues_count
    return 0


def enrich_issues_with_doc_urls(
    tool: BaseToolPlugin,
    result: ToolResult,
) -> None:
    """Populate ``doc_url`` on each issue using the plugin's doc_url method.

    Enriches both remaining issues (``result.issues``) and any pre-fix issues
    (``result.initial_issues``) so the fix-mode "Detected" and "Remaining"
    tables both show doc URLs. Skips issues that already have a doc_url set.

    Args:
        tool: Plugin instance that may provide a doc_url method.
        result: ToolResult whose issues will be enriched in-place.
    """
    if not hasattr(tool, "doc_url"):
        return

    def _enrich(issues: Sequence[BaseIssue] | None) -> None:
        if not issues:
            return
        for issue in issues:
            if getattr(issue, "doc_url", ""):
                continue
            # Resolve the code attribute via DISPLAY_FIELD_MAP so tools
            # that store their identifier under a different name (e.g.
            # advisory_id, vuln_id, rule_id) are handled correctly.
            field_map = getattr(issue, "DISPLAY_FIELD_MAP", {})
            code_attr = field_map.get("code", "code")
            code = str(getattr(issue, code_attr, "") or "")
            if code:
                url = tool.doc_url(code)
                if url:
                    issue.doc_url = url

    _enrich(result.issues)
    _enrich(result.initial_issues)


def issue_would_be_fixed(issue: BaseIssue) -> bool:
    """Return whether a check-mode issue is one that ``fmt`` would auto-fix.

    Resolves the issue's fixability via ``DISPLAY_FIELD_MAP`` (some tools store
    the flag under a different attribute). Tools that carry a per-issue
    fixability signal are honored — only truthy-fixable issues count. For
    example, ruff sets ``fixable`` from ruff's own ``fix`` field, so its
    non-``--fix``-able lint diagnostics are excluded.

    Issue types that expose no ``fixable`` attribute at all (the pure-formatter
    parsers such as prettier, oxfmt, taplo, sqlfluff) carry no fixability
    distinction. Because dry-run only runs fix-capable (formatter) tools, every
    diagnostic such a tool reports in check mode represents a reformat that a
    real ``fmt`` run would apply, so it is treated as fixable.

    Args:
        issue: The parsed issue to classify.

    Returns:
        bool: True if the issue would be fixed by a real ``fmt`` run.
    """
    field_map = getattr(issue, "DISPLAY_FIELD_MAP", {})
    fixable_attr = field_map.get("fixable", "fixable")
    if not hasattr(issue, fixable_attr):
        return True
    return bool(getattr(issue, fixable_attr, False))


def filter_result_to_fixable(result: ToolResult) -> ToolResult:
    """Return a copy of a dry-run check result limited to would-fix issues.

    Filters ``issues`` down to the auto-fixable subset (see
    :func:`issue_would_be_fixed`) and updates ``issues_count`` to match, so
    dry-run counts, the summary line, and the exit code reflect only what a
    real ``fmt`` run would actually change rather than every check-mode
    diagnostic.

    A check-mode tool sets ``success=False`` when it merely *finds* issues, but
    in dry-run those diagnostics are informational: the exit code must derive
    purely from the fixable issue count, not from the tool having found
    (possibly non-fixable) issues. So any result that parsed issues is marked
    ``success=True`` here — it ran fine. Results without parsed issues are
    returned unchanged: a genuine execution failure carries no parsed issues
    and must still fail the run, and dry-run only runs fix-capable tools, so a
    reported change without structured issues is treated as fixable.

    Args:
        result: The check-mode result to filter.

    Returns:
        ToolResult: A filtered copy, or the original when there are no parsed
        issues.
    """
    import dataclasses

    if not result.issues:
        return result
    fixable = [issue for issue in result.issues if issue_would_be_fixed(issue)]
    return dataclasses.replace(
        result,
        issues=fixable,
        issues_count=len(fixable),
        success=True,
    )

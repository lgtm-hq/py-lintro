"""Section lists for the two sticky bodies.

Both bodies — the one rendered from a completed round and the one re-rendered
from persisted state alone — are ordered lists of
:class:`~lintro.ai.review.github_render.Section` values handed to the shared
``assemble`` pipeline (#2304). Neither joins nor caps anything itself, which is
what makes the sticky, the review body and the error comment one pipeline
rather than three that happen to agree.
"""

from __future__ import annotations

from lintro.ai.review.agent_prompts import render_agent_prompt_panel
from lintro.ai.review.enums.agent_prompt_scope_kind import AgentPromptScopeKind
from lintro.ai.review.enums.checklist_display import ChecklistDisplay
from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.github_constants import STICKY_FOOTER, STICKY_MARKER
from lintro.ai.review.github_contract import RenderLimits
from lintro.ai.review.github_render import (
    Section,
    _format_checklist_appendix_markdown,
)
from lintro.ai.review.models.agent_prompt_scope import AgentPromptScope
from lintro.ai.review.models.sticky_plan import StickyPlan
from lintro.ai.review.sticky.cells import _sorted_open_findings
from lintro.ai.review.sticky.findings import (
    _degraded_details,
    _findings_round_section,
)
from lintro.ai.review.sticky.history import _history_section, _this_run_section
from lintro.ai.review.sticky.sections import (
    _coverage_limited_row,
    _coverage_section,
    _cross_chunk_row,
    _degraded_row,
    _header,
    _incomplete_banner,
    _reasoning_section,
    _suggestion_drops_row,
    _summary_section,
)

__all__ = ["round_sections", "state_sections"]


def _history_sections(
    *,
    plan: StickyPlan,
    limits: RenderLimits,
    archive_only: bool = False,
) -> list[Section]:
    """Render the run-history fold and the rule that separates it.

    Args:
        plan: Resolved inputs for the body being rendered.
        limits: Per-section render limits.
        archive_only: When True, history expanders become a link.

    Returns:
        list[Section]: The divider and the history fold, or nothing when
        there is no history to show.
    """
    history = _history_section(
        runs=plan.runs,
        limit=limits.history,
        resolved_total=sum(
            1
            for record in plan.match.records
            if record.status is FindingStatus.RESOLVED
        ),
        archive_only=archive_only,
        records=plan.match.records,
    )
    if not history:
        return []
    return [
        Section(name="divider", text="---"),
        Section(name="history", text=history),
    ]


def state_sections(
    *,
    plan: StickyPlan,
    banner: str,
    limits: RenderLimits,
) -> list[Section]:
    """Order the sections of a board re-rendered from state alone.

    Args:
        plan: Resolved inputs, with ``result`` left as ``None``.
        banner: Optional blockquote rendered directly under the header.
        limits: Per-section render limits.

    Returns:
        list[Section]: The body's sections, top of the comment first.
    """
    return [
        Section(name="marker", text=STICKY_MARKER),
        Section(
            name="header",
            text=_header(
                round_number=plan.round_number,
                head_sha=plan.head_sha,
                verdict=plan.verdict,
            ),
        ),
        Section(name="banner", text=banner),
        Section(
            name="findings_round",
            text=_findings_round_section(plan=plan, limits=limits),
        ),
        *_history_sections(plan=plan, limits=limits),
        Section(name="footer", text=STICKY_FOOTER),
    ]


def round_sections(
    *,
    plan: StickyPlan,
    limits: RenderLimits,
    archive_history: bool = False,
) -> list[Section]:
    """Order every sticky section of a completed round, in mockup order.

    Args:
        plan: Resolved inputs for this round.
        limits: Per-section render limits.
        archive_history: When True, history expanders are replaced by a link.

    Returns:
        list[Section]: The body's sections, top of the comment first.

    Raises:
        ValueError: When the plan carries no result, which is the state-only
            re-render :func:`state_sections` owns.
    """
    result = plan.result
    if result is None:
        msg = "round_sections needs a result; use state_sections instead"
        raise ValueError(msg)
    appendix = (
        "\n".join(_format_checklist_appendix_markdown(result=result))
        if plan.checklist_display is ChecklistDisplay.ALL
        else ""
    )
    return [
        Section(name="marker", text=STICKY_MARKER),
        Section(
            name="header",
            text=_header(
                round_number=plan.round_number,
                head_sha=plan.head_sha,
                verdict=plan.verdict,
            ),
        ),
        Section(
            name="incomplete_banner",
            text=_incomplete_banner(result=result, verdict=plan.verdict),
        ),
        Section(
            name="coverage",
            text=_coverage_section(result=result, verdict=plan.verdict),
        ),
        Section(name="summary", text=_summary_section(result=result)),
        Section(
            name="reasoning",
            text=_reasoning_section(result=result, verdict=plan.verdict),
        ),
        Section(name="degraded_row", text=_degraded_row(failure=plan.inline_failure)),
        Section(name="suggestion_drops", text=_suggestion_drops_row(result=result)),
        Section(name="coverage_limited", text=_coverage_limited_row(result=result)),
        Section(name="cross_chunk", text=_cross_chunk_row(result=result)),
        Section(
            name="findings_round",
            text=_findings_round_section(plan=plan, limits=limits),
        ),
        Section(
            name="degraded_details",
            text=_degraded_details(
                failure=plan.inline_failure,
                checklist_display=plan.checklist_display,
                question_map=plan.question_map,
                limit=limits.open,
            ),
        ),
        Section(
            name="fix_all_prompt",
            text=render_agent_prompt_panel(
                findings=_sorted_open_findings(
                    findings=result.findings,
                    limit=limits.open,
                ),
                scope=AgentPromptScope(
                    kind=AgentPromptScopeKind.ALL_OPEN,
                    round_number=plan.round_number,
                ),
            ),
        ),
        Section(
            name="this_run",
            text=_this_run_section(
                result=result,
                transport=plan.transport,
                auth_mode=plan.auth_mode,
            ),
        ),
        Section(name="checklist_appendix", text=appendix),
        *_history_sections(plan=plan, limits=limits, archive_only=archive_history),
        Section(name="footer", text=STICKY_FOOTER),
    ]

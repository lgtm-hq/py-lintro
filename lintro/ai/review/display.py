"""Rich terminal rendering for AI review results."""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from lintro.ai.cost import format_cost
from lintro.ai.display.shared import cost_str, print_section_header
from lintro.ai.resolved_ai_config import (
    MAX_COST_LABEL,
    format_max_cost_label,
    format_sourced_value,
)
from lintro.ai.review.checklist_display import (
    cleared_answers,
    orphan_concerns,
    questions_for_finding,
)
from lintro.ai.review.coverage_degradation import (
    COVERAGE_LIMITED_HEADLINE,
    describe_coverage_degradations,
)
from lintro.ai.review.enums.checklist_display import ChecklistDisplay
from lintro.ai.review.models.review_finding import ReviewFinding
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.patch_validation import describe_suggestion_drops
from lintro.ai.review.severity_gate import describe_cross_chunk_contradictions
from lintro.ai.review.timings import format_timing_summary

__all__ = ["render_review_terminal"]

_SEVERITY_ORDER = {"P1": 0, "P2": 1, "P3": 2}
_SEVERITY_STYLES = {
    "P1": "bold red",
    "P2": "bold yellow",
    "P3": "bold blue",
}


def render_review_terminal(
    *,
    result: ReviewResult,
    console: Console | None = None,
    checklist_display: ChecklistDisplay = ChecklistDisplay.OFF,
    question_map: dict[int, str] | None = None,
) -> None:
    """Render review result to the terminal with Rich formatting.

    Args:
        result: Review result to display.
        console: Optional Rich console instance.
        checklist_display: Structured checklist visibility mode.
        question_map: Prompt id to question text for linked display.
    """
    output = console or Console()
    metadata = result.metadata
    prompt_questions = question_map or {}

    transport_label = format_sourced_value(
        metadata.transport or "unset",
        metadata.transport_source,
    )
    max_cost_parts = ""
    if metadata.max_cost_usd is not None or metadata.max_cost_usd_source:
        cap_label = format_max_cost_label(
            max_cost_usd=metadata.max_cost_usd,
            source=metadata.max_cost_usd_source,
        )
        max_cost_parts = f" | {MAX_COST_LABEL}: {cap_label}"
    header_detail = (
        f"Model: {format_sourced_value(metadata.model, metadata.model_source)} | "
        f"Provider: "
        f"{format_sourced_value(metadata.provider, metadata.provider_source)} | "
        f"Transport: {transport_label} | "
        f"Context: {metadata.context_window:,} | "
        f"Depth: {metadata.depth} | Strictness: {metadata.strictness} | Chunks: "
        f"{metadata.chunks_current}/{metadata.chunks_total} | "
        f"Files: {metadata.files_reviewed}/{metadata.files_total} | "
        f"Structured checks: {metadata.checklist_items}"
        f"{max_cost_parts}"
    )
    token_info = cost_str(
        metadata.token_usage.get("prompt", 0),
        metadata.token_usage.get("completion", 0),
        metadata.cost_estimate_usd,
    )
    print_section_header(
        output,
        "🔍",
        "Lintro Review",
        header_detail,
        cost_info=token_info or f"   est. {format_cost(metadata.cost_estimate_usd)}",
    )

    if metadata.chunks_total > 1:
        output.print(
            f"[dim]Reviewed in {metadata.chunks_total} semantic chunks[/dim]",
        )

    coverage_note = describe_coverage_degradations(metadata=metadata)
    if coverage_note:
        # No silent caps: a capped run must never look like a clean one.
        output.print(
            f"[bold yellow]⚠ {COVERAGE_LIMITED_HEADLINE}[/bold yellow]",
        )
        output.print(f"[yellow]{coverage_note}[/yellow]")

    if metadata.timings is not None:
        # One line, always on: which phase dominated the wait (#2148).
        output.print(
            f"[dim]{format_timing_summary(timings=metadata.timings)}[/dim]",
        )

    output.print(
        Panel(
            result.summary or "(no summary)",
            title="Summary",
            border_style="cyan",
        ),
    )

    show_linked = checklist_display in {ChecklistDisplay.LINKED, ChecklistDisplay.ALL}
    _render_findings(
        result=result,
        console=output,
        show_linked_questions=show_linked,
        question_map=prompt_questions,
    )

    if checklist_display == ChecklistDisplay.ALL:
        _render_checklist_appendix(result=result, console=output)


def _render_findings(
    *,
    result: ReviewResult,
    console: Console,
    show_linked_questions: bool,
    question_map: dict[int, str],
) -> None:
    """Render findings grouped by severity."""
    if not result.findings:
        console.print("[dim]No findings.[/dim]")
        return

    sorted_findings = sorted(
        result.findings,
        key=lambda finding: (
            _SEVERITY_ORDER.get(finding.severity, 99),
            finding.file,
            finding.line,
        ),
    )

    console.print()
    console.print(f"[bold cyan]Findings ({len(sorted_findings)})[/bold cyan]")
    drops = describe_suggestion_drops(findings=result.findings)
    if drops:
        console.print(f"[yellow]{drops}[/yellow]")
    contradictions = describe_cross_chunk_contradictions(findings=result.findings)
    if contradictions:
        # No silent edits: a guard-driven downgrade is stated where the
        # severities it changed are read (#2265).
        console.print(f"[yellow]{contradictions}[/yellow]")

    for index, finding in enumerate(sorted_findings, start=1):
        _render_finding_panel(
            finding=finding,
            index=index,
            total=len(sorted_findings),
            console=console,
            show_linked_questions=show_linked_questions,
            question_map=question_map,
        )


def _render_finding_panel(
    *,
    finding: ReviewFinding,
    index: int,
    total: int,
    console: Console,
    show_linked_questions: bool,
    question_map: dict[int, str],
) -> None:
    """Render a single finding as a Rich panel."""
    severity_style = _SEVERITY_STYLES.get(finding.severity, "white")
    source_chip = f"  [dim]via {finding.source}[/dim]" if finding.source else ""
    title = (
        f"[{severity_style}]{finding.severity}[/{severity_style}]  "
        f"{finding.category}  "
        f"{finding.file}:{finding.line}  "
        f"[dim]({finding.confidence})[/dim]"
        f"{source_chip}"
    )
    body = Text()
    body.append(f"{finding.title}\n\n", style="bold")
    body.append(f"{finding.description}\n\n")
    body.append("Cause: ", style="bold")
    body.append(f"{finding.cause}\n\n")
    body.append("Fix: ", style="bold")
    body.append(finding.fix)

    if finding.suggestion_dropped is not None:
        body.append("\n\n")
        body.append("Suggestion dropped: ", style="bold yellow")
        body.append(
            f"{finding.suggestion_dropped} "
            "(did not match the file at head; fix text kept, "
            "one-click commit withheld)",
            style="yellow",
        )

    if show_linked_questions:
        linked_questions = questions_for_finding(
            finding=finding,
            question_map=question_map,
        )
        if linked_questions:
            body.append("\n\n")
            body.append("Review questions:\n", style="bold")
            for question in linked_questions:
                body.append(f"  • {question}\n")

    console.print(
        Panel(
            body,
            title=f"[bold cyan][{index}/{total}][/bold cyan] {title}",
            title_align="left",
            border_style="cyan",
            padding=(0, 1),
        ),
    )


def _render_checklist_appendix(*, result: ReviewResult, console: Console) -> None:
    """Render cleared and orphan checklist sections for audit mode."""
    cleared = cleared_answers(answers=result.checklist)
    orphans = orphan_concerns(
        answers=result.checklist,
        findings=result.findings,
    )

    console.print()
    console.print(f"[bold cyan]Cleared checks ({len(cleared)})[/bold cyan]")
    if cleared:
        for answer in cleared:
            question = answer.question or f"(checklist item {answer.id})"
            console.print(f"  [green]✓[/green] {question}")
    else:
        console.print("[dim]  (none)[/dim]")

    console.print()
    console.print(
        f"[bold cyan]Checklist concerns without findings ({len(orphans)})[/bold cyan]",
    )
    if orphans:
        for answer in orphans:
            question = answer.question or f"(checklist item {answer.id})"
            console.print(f"  [yellow]•[/yellow] {question}")
            if answer.evidence.strip():
                evidence = answer.evidence
                if len(evidence) > 120:
                    evidence = f"{evidence[:117]}..."
                console.print(f"    [dim]{evidence}[/dim]")
    else:
        console.print("[dim]  (none — good)[/dim]")

"""Rich terminal rendering for AI review results."""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from lintro.ai.cost import format_cost
from lintro.ai.display.shared import cost_str, print_section_header
from lintro.ai.review.checklist_display import (
    cleared_answers,
    orphan_concerns,
    questions_for_finding,
)
from lintro.ai.review.enums.checklist_display import ChecklistDisplay
from lintro.ai.review.models.review_finding import ReviewFinding
from lintro.ai.review.models.review_result import ReviewResult

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

    header_detail = (
        f"Model: {metadata.model} | Context: {metadata.context_window:,} | "
        f"Depth: {metadata.depth} | Strictness: {metadata.strictness} | Chunks: "
        f"{metadata.chunks_current}/{metadata.chunks_total} | "
        f"Files: {metadata.files_reviewed}/{metadata.files_total} | "
        f"Structured checks: {metadata.checklist_items}"
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
    title = (
        f"[{severity_style}]{finding.severity}[/{severity_style}]  "
        f"{finding.category}  "
        f"{finding.file}:{finding.line}  "
        f"[dim]({finding.confidence})[/dim]"
    )
    body = Text()
    body.append(f"{finding.title}\n\n", style="bold")
    body.append(f"{finding.description}\n\n")
    body.append("Cause: ", style="bold")
    body.append(f"{finding.cause}\n\n")
    body.append("Fix: ", style="bold")
    body.append(finding.fix)

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

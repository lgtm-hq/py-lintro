"""Rich terminal rendering for AI review results."""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from lintro.ai.cost import format_cost
from lintro.ai.display.shared import cost_str, print_section_header
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
) -> None:
    """Render review result to the terminal with Rich formatting.

    Args:
        result: Review result to display.
        console: Optional Rich console instance.
    """
    output = console or Console()
    metadata = result.metadata

    header_detail = (
        f"Model: {metadata.model} | Context: {metadata.context_window:,} | "
        f"Depth: {metadata.depth} | Strictness: {metadata.strictness} | Chunks: "
        f"{metadata.chunks_current}/{metadata.chunks_total} | "
        f"Files: {metadata.files_reviewed}/{metadata.files_total} | "
        f"Checklist: {metadata.checklist_items} items"
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

    _render_checklist_table(result=result, console=output)
    _render_findings(result=result, console=output)


def _render_checklist_table(*, result: ReviewResult, console: Console) -> None:
    """Render checklist answers as a Rich table."""
    if not result.checklist:
        return

    table = Table(title="Checklist", show_header=True, header_style="bold cyan")
    table.add_column("ID", justify="right", style="cyan", no_wrap=True)
    table.add_column("Answer", no_wrap=True)
    table.add_column("Evidence")

    for answer in result.checklist:
        answer_style = "red" if answer.answer.lower() == "yes" else "green"
        evidence = answer.evidence
        if len(evidence) > 120:
            evidence = f"{evidence[:117]}..."
        table.add_row(
            str(answer.id),
            Text(answer.answer, style=answer_style),
            evidence,
        )

    console.print(table)


def _render_findings(*, result: ReviewResult, console: Console) -> None:
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
        )


def _render_finding_panel(
    *,
    finding: ReviewFinding,
    index: int,
    total: int,
    console: Console,
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

    console.print(
        Panel(
            body,
            title=f"[bold cyan][{index}/{total}][/bold cyan] {title}",
            title_align="left",
            border_style="cyan",
            padding=(0, 1),
        ),
    )

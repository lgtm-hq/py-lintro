"""Interactive fix review loop for AI-generated suggestions.

Used by the ``fmt`` flow when ``auto_apply`` is False. Groups suggestions
by error code and presents them for batch accept/reject decisions.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from collections.abc import Sequence
from enum import StrEnum
from pathlib import Path

import click
from rich.console import Console, Group, RenderableType
from rich.markup import escape
from rich.panel import Panel
from rich.syntax import Syntax

from lintro.ai.apply import apply_fixes, rollback_applied_paths
from lintro.ai.display.shared import cost_str, print_code_panel, print_section_header
from lintro.ai.display.validation import render_validation
from lintro.ai.enums import RiskLevel
from lintro.ai.models import AIFixSuggestion
from lintro.ai.paths import relative_path
from lintro.ai.risk import (
    SAFE_STYLE_RISK,
    calculate_patch_stats,
    classify_fix_risk,
    is_safe_style_fix,
)
from lintro.ai.undo import UndoState
from lintro.ai.validation import validate_applied_fixes

__all__ = ["apply_fixes", "review_fixes_interactive"]


class ReviewKey(StrEnum):
    """Key bindings for interactive fix review."""

    ACCEPT = "y"
    ACCEPT_ALL = "a"
    REJECT = "r"
    SHOW_DIFF = "d"
    SKIP = "s"
    TOGGLE_VALIDATE = "v"
    QUIT = "q"


def _group_by_code(
    suggestions: Sequence[AIFixSuggestion],
) -> dict[str, list[AIFixSuggestion]]:
    """Group fix suggestions by error code.

    Args:
        suggestions: Fix suggestions to group.

    Returns:
        Dict mapping error code to list of suggestions.
    """
    groups: dict[str, list[AIFixSuggestion]] = defaultdict(list)
    for s in suggestions:
        key = s.code or "unknown"
        groups[key].append(s)
    return dict(groups)


def _print_group_header(
    console: Console,
    code: str,
    fixes: list[AIFixSuggestion],
    group_index: int,
    total_groups: int,
) -> None:
    """Print a panel for one error-code group.

    Delegates to the shared ``print_code_panel`` from display.py
    to ensure consistent Panel styling across chk and fmt.

    Args:
        console: Rich Console instance.
        code: The error code (e.g. "D107").
        fixes: Suggestions in this group.
        group_index: 1-based index of this group.
        total_groups: Total number of groups.
    """
    parts: list[RenderableType] = []
    stats = calculate_patch_stats(fixes)
    risk_labels = {classify_fix_risk(fix) for fix in fixes}
    group_risk = (
        SAFE_STYLE_RISK
        if len(risk_labels) == 1 and SAFE_STYLE_RISK in risk_labels
        else RiskLevel.BEHAVIORAL_RISK
    )
    risk_color = "green" if group_risk == SAFE_STYLE_RISK else "yellow"

    parts.append(
        (
            f"[{risk_color}]risk: {group_risk}[/{risk_color}]"
            "  ·  "
            f"[dim]patch: {stats.files} files, +{stats.lines_added}/"
            f"-{stats.lines_removed}, {stats.hunks} hunks[/dim]"
        ),
    )

    first = fixes[0] if fixes else None
    explanation = (first.explanation or "") if first else ""
    if explanation:
        parts.append(f"[cyan]{escape(explanation)}[/cyan]")

    for fix in fixes:
        rel = relative_path(fix.file)
        loc = f"{rel}:{fix.line}" if fix.line else rel
        parts.append(
            Panel(
                f"[green]{escape(loc)}[/green]",
                border_style="dim",
                padding=(0, 1),
            ),
        )

    content: RenderableType = (
        Group(*parts) if len(parts) > 1 else (parts[0] if parts else "")
    )
    console.print()
    group_tool = first.tool_name if first else ""
    unique_files = len({fix.file for fix in fixes})
    print_code_panel(
        console,
        code=code,
        index=group_index,
        total=total_groups,
        count=unique_files,
        count_label="file",
        content=content,
        tool_name=group_tool,
    )


def _show_group_diffs(
    console: Console,
    fixes: list[AIFixSuggestion],
) -> None:
    """Show individual diffs for a group of fixes.

    Args:
        console: Rich Console instance.
        fixes: Suggestions to show diffs for.
    """
    for fix in fixes:
        if not fix.diff or not fix.diff.strip():
            continue

        rel = relative_path(fix.file)
        loc = f"{rel}:{fix.line}" if fix.line else rel
        console.print(f"\n  [dim]{loc}[/dim]")

        syntax = Syntax(
            fix.diff,
            "diff",
            theme="ansi_dark",
            padding=0,
        )
        console.print(syntax)


def _apply_group(
    console: Console,
    fixes: list[AIFixSuggestion],
    *,
    workspace_root: Path,
    search_radius: int = 5,
) -> tuple[int, list[AIFixSuggestion]]:
    """Apply all fixes in a group, reporting results.

    Args:
        console: Rich Console instance.
        fixes: Suggestions to apply.
        workspace_root: Root directory limiting writable paths.
        search_radius: Max lines above/below the target line to search.

    Returns:
        Tuple of (applied_count, list of successfully applied suggestions).
    """
    # Sort fixes by (file, line descending) so same-file edits apply
    # bottom-to-top, preventing earlier edits from shifting later targets.
    sorted_fixes = sorted(fixes, key=lambda f: (f.file, -(f.line or 0)))
    applied_fixes = apply_fixes(
        sorted_fixes,
        workspace_root=workspace_root,
        search_radius=search_radius,
    )
    applied = len(applied_fixes)
    failed = len(fixes) - applied
    msg = f"  [green]✓ Applied {applied}/{len(fixes)}[/green]"
    if failed:
        msg += f"  [yellow]({failed} failed)[/yellow]"
    console.print(msg)
    return applied, applied_fixes


def _validate_group(
    console: Console,
    applied_suggestions: Sequence[AIFixSuggestion],
) -> None:
    """Run validation immediately for a single accepted group."""
    validation = validate_applied_fixes(applied_suggestions)
    if not validation:
        return
    if (
        validation.verified == 0
        and validation.unverified == 0
        and not validation.new_issues
        and not validation.details
    ):
        return
    output = render_validation(validation)
    if output:
        console.print(output)


def _render_prompt(*, validate_mode: bool, safe_default: bool) -> str:
    """Build interactive prompt text with current mode/default."""
    default_text = " (Enter=accept group; safe-style default)" if safe_default else ""
    mode = "on" if validate_mode else "off"
    return (
        "  [y]accept group  [a]accept group + remaining  "
        "[r]reject  [d]diffs  [s]skip  [v]verify fixes:"
        f" {mode} (toggle only, no apply)  [q]quit{default_text}: "
    )


def review_fixes_interactive(
    suggestions: Sequence[AIFixSuggestion],
    *,
    validate_after_group: bool = False,
    workspace_root: Path,
    search_radius: int = 5,
    undo_state: UndoState | None = None,
) -> tuple[int, int, list[AIFixSuggestion]]:
    """Present fix suggestions grouped by error code for review.

    Groups suggestions by error code and prompts once per group:
    ``[y]accept group / [a]accept group + remaining / [r]eject /
    [d]iffs / [s]kip / [v]toggle per-group validation / [q]uit``

    When ``undo_state`` is provided, rejecting a group restores that group's
    target files from the pre-batch git checkpoint (or file-snapshot
    fallback) rather than from in-memory suggestion copies. Restore is
    idempotent when the files were never mutated.

    Args:
        suggestions: Fix suggestions to review.
        validate_after_group: Whether to validate immediately after
            each accepted group.
        workspace_root: Root directory limiting writable paths.
        search_radius: Max lines above/below the target line to search.
        undo_state: Optional pre-batch checkpoint / snapshot for reject
            rollback.

    Returns:
        Tuple of (accepted_count, rejected_count, applied_suggestions).
    """
    if not suggestions:
        return 0, 0, []

    # Non-interactive environments skip the review
    if not sys.stdin.isatty():
        return 0, 0, []

    console = Console()
    accepted = 0
    rejected = 0
    accept_all = False
    validate_mode = validate_after_group
    all_applied: list[AIFixSuggestion] = []

    groups = _group_by_code(suggestions)
    total_groups = len(groups)
    total_fixes = len(suggestions)
    plural = "es" if total_fixes != 1 else ""

    # Section header
    total_input = sum(s.input_tokens for s in suggestions)
    total_output = sum(s.output_tokens for s in suggestions)
    total_cost = sum(s.cost_estimate for s in suggestions)
    codes = f"{total_groups} code{'s' if total_groups != 1 else ''}"
    cost_info = cost_str(total_input, total_output, total_cost)
    print_section_header(
        console,
        "🤖",
        "AI FIX SUGGESTIONS",
        f"{total_fixes} fix{plural} across {codes}",
        cost_info=cost_info,
    )

    auto_accepted = 0
    auto_failed = 0
    auto_groups = 0

    for gi, (code, fixes) in enumerate(groups.items(), 1):
        if accept_all:
            count, group_applied = _apply_group(
                console,
                fixes,
                workspace_root=workspace_root,
                search_radius=search_radius,
            )
            accepted += count
            auto_accepted += count
            auto_failed += len(fixes) - count
            auto_groups += 1
            all_applied.extend(group_applied)
            if validate_mode:
                if group_applied:
                    _validate_group(console, group_applied)
                else:
                    console.print(
                        "  [dim]Validation skipped "
                        "(no fixes applied in this group).[/dim]",
                    )
            continue

        # Group header (flat text, no panels)
        _print_group_header(console, code, fixes, gi, total_groups)

        safe_default = all(is_safe_style_fix(fix) for fix in fixes)
        console.print()

        while True:
            prompt_text = click.style(
                _render_prompt(
                    validate_mode=validate_mode,
                    safe_default=safe_default,
                ),
                fg="cyan",
            )
            click.echo(prompt_text, nl=False)
            try:
                choice = click.getchar()
                click.echo(choice)  # echo the keypress
            except (EOFError, KeyboardInterrupt):
                click.echo()
                return accepted, rejected, all_applied

            if choice in ("\r", "\n"):
                choice = ReviewKey.ACCEPT if safe_default else ReviewKey.SKIP
            else:
                choice = choice.lower()

            if choice == ReviewKey.SHOW_DIFF:
                _show_group_diffs(console, fixes)
                console.print()
                continue
            if choice == ReviewKey.TOGGLE_VALIDATE:
                validate_mode = not validate_mode
                state = "enabled" if validate_mode else "disabled"
                console.print(
                    f"  [dim]Per-group validation {state} " "(no fixes applied).[/dim]",
                )
                console.print()
                continue

            _valid_actions = {v.value for v in ReviewKey}
            if choice not in _valid_actions:
                console.print("  [dim]Unrecognized key. Use y/a/r/d/s/v/q.[/dim]")
                console.print()
                continue

            break

        if choice in (ReviewKey.ACCEPT_ALL, ReviewKey.ACCEPT):
            count, group_applied = _apply_group(
                console,
                fixes,
                workspace_root=workspace_root,
                search_radius=search_radius,
            )
            accepted += count
            all_applied.extend(group_applied)
            if validate_mode:
                if group_applied:
                    _validate_group(console, group_applied)
                else:
                    console.print(
                        "  [dim]Validation skipped "
                        "(no fixes applied in this group).[/dim]",
                    )
            if choice == ReviewKey.ACCEPT_ALL:
                accept_all = True
                console.print("  [dim]Will accept all remaining groups.[/dim]")
        elif choice == ReviewKey.REJECT:
            if undo_state is not None:
                # Per-file rejection restores from the checkpoint tree (or
                # file-snapshot fallback), not from in-memory originals.
                rollback_applied_paths(undo_state, fixes)
                # Drop any earlier accepts that touched the same paths.
                rejected_files = {f.file for f in fixes if f.file}
                before = len(all_applied)
                all_applied[:] = [
                    s for s in all_applied if s.file not in rejected_files
                ]
                accepted = max(0, accepted - (before - len(all_applied)))
            rejected += len(fixes)
            console.print(
                f"  [yellow]✗ Rejected {len(fixes)} "
                f"fix{'es' if len(fixes) != 1 else ''}[/yellow]",
            )
        elif choice == ReviewKey.SKIP:
            console.print("  [dim]⏭  Skipped[/dim]")
        elif choice == ReviewKey.QUIT:
            console.print("  [dim]Quit review.[/dim]")
            break

    # Consolidated line for auto-accepted groups
    if auto_groups > 0:
        total_auto = auto_accepted + auto_failed
        msg = (
            f"  [green]✓ Applied {auto_accepted}/{total_auto} "
            f"across {auto_groups} group{'s' if auto_groups != 1 else ''}[/green]"
        )
        if auto_failed:
            msg += f"  [yellow]({auto_failed} failed)[/yellow]"
        console.print(msg)

    # Summary
    console.print()
    parts: list[str] = []
    if accepted:
        parts.append(f"[green]{accepted} accepted[/green]")
    if rejected:
        parts.append(f"[red]{rejected} rejected[/red]")
    skipped = total_fixes - accepted - rejected
    if skipped:
        parts.append(f"{skipped} skipped")
    if parts:
        console.print(
            f"  [bold]Review complete:[/bold] {' · '.join(parts)}",
        )
    console.print()

    return accepted, rejected, all_applied

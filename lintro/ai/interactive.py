"""Interactive fix review loop for AI-generated suggestions.

Used by the ``fmt`` flow when ``auto_apply`` is False. Groups suggestions
by error code and presents them for batch accept/reject decisions.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path

import click
from loguru import logger
from rich.console import Console, Group, RenderableType
from rich.markup import escape
from rich.panel import Panel
from rich.syntax import Syntax

from lintro.ai.display import (
    _cost_str,
    _print_code_panel,
    _print_section_header,
    render_validation,
)
from lintro.ai.models import AIFixSuggestion
from lintro.ai.paths import relative_path, resolve_workspace_file
from lintro.ai.risk import (
    SAFE_STYLE_RISK,
    calculate_patch_stats,
    classify_fix_risk,
    is_safe_style_fix,
)
from lintro.ai.validation import validate_applied_fixes


def _apply_fix(
    suggestion: AIFixSuggestion,
    *,
    workspace_root: Path | None = None,
) -> bool:
    """Apply a single fix suggestion to the file.

    Uses line-number-targeted replacement to avoid matching the wrong
    occurrence when the same code pattern appears elsewhere in the file.
    Falls back to first-occurrence replacement if line targeting fails.

    Args:
        suggestion: Fix suggestion to apply.
        workspace_root: Optional root directory limiting writable paths.

    Returns:
        True if the fix was applied successfully.
    """
    try:
        path = Path(suggestion.file)
        if workspace_root is not None:
            resolved = resolve_workspace_file(suggestion.file, workspace_root)
            if resolved is None:
                return False
            path = resolved
        content = path.read_text(encoding="utf-8")
        lines = content.splitlines(keepends=True)

        original_lines = suggestion.original_code.splitlines(keepends=True)
        if not original_lines:
            return False

        # Ensure last line has consistent newline for comparison
        if original_lines and not original_lines[-1].endswith("\n"):
            original_lines[-1] += "\n"

        # Search outward from the target line (closest match wins)
        target_idx = max(0, suggestion.line - 1)  # 0-based
        search_radius = 5
        search_order = [target_idx]
        for offset in range(1, search_radius + 1):
            if target_idx - offset >= 0:
                search_order.append(target_idx - offset)
            if target_idx + offset < len(lines):
                search_order.append(target_idx + offset)

        for start in search_order:
            end = start + len(original_lines)
            if end > len(lines):
                continue

            window = lines[start:end]
            # Normalize trailing newline on last window line for comparison
            normalized_window = list(window)
            if normalized_window and not normalized_window[-1].endswith("\n"):
                normalized_window[-1] += "\n"

            if normalized_window == original_lines:
                suggested_lines = suggestion.suggested_code.splitlines(
                    keepends=True,
                )
                # Preserve trailing newline consistency
                if (
                    suggested_lines
                    and window
                    and window[-1].endswith("\n")
                    and not suggested_lines[-1].endswith("\n")
                ):
                    suggested_lines[-1] += "\n"

                new_lines = lines[:start] + suggested_lines + lines[end:]
                path.write_text("".join(new_lines), encoding="utf-8")
                return True

        # Fallback: first-occurrence string replacement.
        # Warning: this may apply the fix to the wrong location if
        # the original code appears earlier in the file.
        if suggestion.original_code in content:
            logger.warning(
                f"Line-targeted replacement failed for "
                f"{suggestion.file}:{suggestion.line}, "
                f"falling back to first-occurrence string replacement",
            )
            new_content = content.replace(
                suggestion.original_code,
                suggestion.suggested_code,
                1,
            )
            path.write_text(new_content, encoding="utf-8")
            return True

        return False

    except OSError:
        return False


def apply_fixes(
    suggestions: Sequence[AIFixSuggestion],
    *,
    workspace_root: Path | None = None,
) -> list[AIFixSuggestion]:
    """Apply suggestions and return only those successfully applied."""
    return [
        fix for fix in suggestions if _apply_fix(fix, workspace_root=workspace_root)
    ]


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

    Delegates to the shared ``_print_code_panel`` from display.py
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
        else "behavioral-risk"
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

    explanation = fixes[0].explanation or ""
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
    # Use tool_name from first suggestion in group
    group_tool = fixes[0].tool_name if fixes else ""
    _print_code_panel(
        console,
        code=code,
        index=group_index,
        total=total_groups,
        count=len(fixes),
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
    workspace_root: Path | None = None,
) -> tuple[int, list[AIFixSuggestion]]:
    """Apply all fixes in a group, reporting results.

    Args:
        console: Rich Console instance.
        fixes: Suggestions to apply.
        workspace_root: Optional root directory limiting writable paths.

    Returns:
        Tuple of (applied_count, list of successfully applied suggestions).
    """
    applied_fixes: list[AIFixSuggestion] = []
    for fix in fixes:
        if _apply_fix(fix, workspace_root=workspace_root):
            applied_fixes.append(fix)
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
    if validation.verified == 0 and validation.unverified == 0:
        return
    output = render_validation(validation)
    if output:
        console.print(output)


def _render_prompt(*, validate_mode: bool, safe_default: bool) -> str:
    """Build interactive prompt text with current mode/default."""
    default_text = (
        " [dim](Enter=accept group; safe-style default)[/dim]" if safe_default else ""
    )
    mode = "on" if validate_mode else "off"
    return (
        "  [y]accept group  [a]accept group + remaining  "
        "[r]reject  [d]diffs  [s]skip  [v]validate-after-group:"
        f" {mode} (toggle only, no apply)  [q]quit{default_text}: "
    )


def review_fixes_interactive(
    suggestions: Sequence[AIFixSuggestion],
    *,
    validate_after_group: bool = False,
    workspace_root: Path | None = None,
) -> tuple[int, int, list[AIFixSuggestion]]:
    """Present fix suggestions grouped by error code for review.

    Groups suggestions by error code and prompts once per group:
    ``[y]accept group / [a]accept group + remaining / [r]eject /
    [d]iffs / [s]kip / [v]toggle per-group validation / [q]uit``

    Args:
        suggestions: Fix suggestions to review.
        validate_after_group: Whether to validate immediately after
            each accepted group.
        workspace_root: Optional root directory limiting writable paths.

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
    cost_info = _cost_str(total_input, total_output, total_cost)
    _print_section_header(
        console,
        "🤖",
        "AI FIX SUGGESTIONS",
        f"{total_fixes} fix{plural} across {codes}",
        cost_info=cost_info,
    )

    for gi, (code, fixes) in enumerate(groups.items(), 1):
        if accept_all:
            count, group_applied = _apply_group(
                console,
                fixes,
                workspace_root=workspace_root,
            )
            accepted += count
            all_applied.extend(group_applied)
            if validate_mode and group_applied:
                _validate_group(console, group_applied)
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
                choice = "y" if safe_default else "s"
            else:
                choice = choice.lower()

            if choice == "d":
                _show_group_diffs(console, fixes)
                console.print()
                continue
            if choice == "v":
                validate_mode = not validate_mode
                state = "enabled" if validate_mode else "disabled"
                console.print(
                    f"  [dim]Per-group validation {state} " "(no fixes applied).[/dim]",
                )
                console.print()
                continue

            break

        if choice == "a":
            count, group_applied = _apply_group(
                console,
                fixes,
                workspace_root=workspace_root,
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
            accept_all = True
            console.print("  [dim]Will accept all remaining groups.[/dim]")
        elif choice == "y":
            count, group_applied = _apply_group(
                console,
                fixes,
                workspace_root=workspace_root,
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
        elif choice == "r":
            rejected += len(fixes)
            console.print(
                f"  [yellow]✗ Rejected {len(fixes)} "
                f"fix{'es' if len(fixes) != 1 else ''}[/yellow]",
            )
        elif choice == "s":
            console.print("  [dim]⏭  Skipped[/dim]")
        elif choice == "q":
            console.print("  [dim]Quit review.[/dim]")
            break

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

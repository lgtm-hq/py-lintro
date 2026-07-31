"""Pre-execution steps of a Lintro run.

Everything that happens between "the user asked for a run" and "the first tool
executes": reporting tools that were skipped, resolving the ``--diff`` base
ref, and printing the configuration summary with its confirmation prompt.

These steps emit console output but produce no results, so they are kept out
of both the execute phase and the render phase (issue #1823).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lintro.utils.execution.tool_configuration import SkippedTool

# Substrings in a skip reason that mean "the tool is not installed".
_MISSING_TOOL_KEYWORDS: tuple[str, ...] = ("not found", "missing")


@dataclass(frozen=True)
class DiffScope:
    """Outcome of resolving the ``--diff`` base ref for a run.

    Attributes:
        base: The resolved base ref to diff against, or ``None`` to scan every
            file.
        failed: Whether resolution failed with an explicit, unusable ref. The
            caller must abort the run with exit code 1 when this is set.
    """

    base: str | None = None
    failed: bool = False


def report_skipped_tools(
    *,
    skipped_tools: list[SkippedTool],
    output_format: str,
    logger: Any,
) -> None:
    """Tell the user that every selected tool was skipped.

    When all skips are "tool not installed" and the run is interactive with a
    human-readable format, the richer first-run guidance is shown instead of a
    bare list.

    Args:
        skipped_tools: The tools that were skipped, with their reasons.
        output_format: Requested output format; machine-readable formats never
            get the interactive guidance.
        logger: Console logger used for the fallback message.
    """
    from lintro.enums.output_format import OutputFormat, normalize_output_format

    all_missing = all(
        st.reason
        and any(keyword in st.reason.lower() for keyword in _MISSING_TOOL_KEYWORDS)
        for st in skipped_tools
    )
    fmt = normalize_output_format(output_format)
    machine_readable = fmt in (OutputFormat.JSON, OutputFormat.SARIF)

    if all_missing and not machine_readable:
        from lintro.cli_utils.onboarding import (
            is_interactive_tty,
            print_first_run_guidance,
        )

        if is_interactive_tty():
            from rich.console import Console

            print_first_run_guidance(Console())
            return

    skipped_names = ", ".join(st.name for st in skipped_tools)
    logger.console_output(
        f"All tools were skipped ({len(skipped_tools)}): {skipped_names}",
    )


def resolve_diff_scope(
    *,
    diff_base: str | None,
    paths: list[str],
    logger: Any,
) -> DiffScope:
    """Resolve the git base ref for ``--diff`` and report the scan scope.

    Non-git directories and unresolvable default refs fall back to a full scan
    with a warning; an explicit but unresolvable ref is a hard error. Scan
    targets may span multiple repositories; each repo's diff is computed
    independently.

    Args:
        diff_base: Raw ``--diff`` value, or ``None`` when ``--diff`` was not
            requested.
        paths: Scan targets supplied by the caller.
        logger: Console logger for warnings and the changed-file count.

    Returns:
        DiffScope: The resolved base ref, or a failed scope when an explicit
        ref could not be resolved.
    """
    if diff_base is None:
        return DiffScope()

    from lintro.utils.git_diff import (
        DIFF_DEFAULT_SENTINEL,
        DiffResolutionError,
        all_repo_defaults_resolvable,
        get_changed_files_for_paths,
        is_git_repository,
        resolve_git_cwd_from_paths,
    )

    repo_groups = resolve_git_cwd_from_paths(paths)
    has_repo_paths = any(root is not None for root in repo_groups)
    resolved_base: str | None = None

    if not has_repo_paths and not is_git_repository():
        logger.console_output(
            text="--diff requested but not inside a git repository; "
            "scanning all files.",
            color="yellow",
        )
    elif diff_base == DIFF_DEFAULT_SENTINEL:
        if all_repo_defaults_resolvable(paths):
            resolved_base = DIFF_DEFAULT_SENTINEL
        else:
            logger.console_output(
                text="--diff: could not resolve a default base ref in every "
                "repository (tried origin/HEAD, origin/main, main, ...); "
                "scanning all files.",
                color="yellow",
            )
    else:
        resolved_base = diff_base

    if resolved_base is None:
        return DiffScope()

    try:
        changed = get_changed_files_for_paths(resolved_base, paths)
    except DiffResolutionError as exc:
        logger.console_output(text=f"Error: {exc}", color="red")
        return DiffScope(failed=True)

    # Non-repo scan targets are scanned in full (they have no diff to filter
    # against), but the changed-file count only covers the repository targets.
    # Warn so the count below isn't read as the whole scan scope when targets
    # are mixed (#1618).
    non_repo_targets = repo_groups.get(None)
    if non_repo_targets and has_repo_paths:
        logger.console_output(
            text=(
                f"--diff: {len(non_repo_targets)} scan target(s) are outside a "
                "git repository and are scanned in full (not diff-filtered); "
                "the changed-file count below counts only the repository "
                "target(s)."
            ),
            color="yellow",
        )

    display_base = (
        "default base" if resolved_base == DIFF_DEFAULT_SENTINEL else resolved_base
    )
    logger.console_output(
        text=f"Diff mode: scanning {len(changed)} file(s) changed vs {display_base}",
        color="cyan",
    )
    return DiffScope(base=resolved_base)


def confirm_pre_execution(
    *,
    tools_to_run: list[str],
    skipped_tools: list[SkippedTool],
    lintro_config: Any,
    effective_auto_install: bool,
    is_container: bool,
    ai_status_lines: list[str] | None,
    logger: Any,
    yes: bool,
) -> bool:
    """Print the configuration summary and ask the user to proceed.

    Args:
        tools_to_run: Tools selected for the main phase.
        skipped_tools: Tools that will not run, with their reasons.
        lintro_config: Loaded Lintro configuration.
        effective_auto_install: Resolved auto-install setting for the run.
        is_container: Whether Lintro is running inside a container.
        ai_status_lines: Pre-rendered AI rows for the summary table, or
            ``None`` when the caller supplied no AI layer.
        logger: Console logger used for the abort message.
        yes: Whether the caller pre-approved the run.

    Returns:
        bool: ``True`` when the run should proceed, ``False`` when the user
        declined at the prompt.
    """
    import sys

    from lintro.utils.console.pre_execution_summary import print_pre_execution_summary
    from lintro.utils.environment import detect_ci_environment

    # Collect per-tool auto_install settings
    per_tool_auto: dict[str, bool | None] = {}
    for name in tools_to_run:
        tool_cfg = lintro_config.get_tool_config(name)
        if tool_cfg.auto_install is not None:
            per_tool_auto[name] = tool_cfg.auto_install

    ci_env = detect_ci_environment()
    is_ci = ci_env is not None and ci_env.is_ci
    print_pre_execution_summary(
        tools_to_run=tools_to_run,
        skipped_tools=skipped_tools,
        effective_auto_install=effective_auto_install,
        is_container=is_container,
        is_ci=is_ci,
        per_tool_auto_install=per_tool_auto if per_tool_auto else None,
        ai_status_lines=ai_status_lines,
    )

    # Confirmation prompt — skip when non-interactive
    if yes or is_ci or not sys.stdin.isatty():
        return True

    import click as _click

    _click.echo("Proceed? [Y/n] ", nl=False)
    try:
        answer = _click.getchar()
        _click.echo(answer)  # echo the keypress
    except (EOFError, KeyboardInterrupt):
        _click.echo()
        answer = "n"
    if answer.lower() == "n":
        logger.console_output(text="Aborted.", color="yellow")
        return False
    return True

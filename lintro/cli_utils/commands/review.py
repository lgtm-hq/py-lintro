"""CLI command for AI diff-based code review."""

from __future__ import annotations

import os

import click
from loguru import logger
from rich.console import Console

from lintro.ai.availability import require_ai
from lintro.ai.exceptions import AIError
from lintro.ai.providers import get_provider
from lintro.ai.transport import apply_transport_override
from lintro.ai.review import (
    classify_changed_files,
    collect_review_context,
    format_checklist_for_prompt,
    get_all_checklist_items,
    select_checklist_items,
)
from lintro.ai.review.checklist_display import (
    build_prompt_question_map,
    enrich_review_result,
    resolve_checklist_display,
)
from lintro.ai.review.enums.review_strictness import ReviewStrictness
from lintro.ai.review.error_display import render_review_error
from lintro.ai.review.exceptions import ReviewContextError
from lintro.ai.review.orchestrator import run_review
from lintro.ai.review.output import render_review_output
from lintro.ai.review.sensitivity import resolve_sensitivity_policy
from lintro.config.config_loader import get_config


@click.command("review")
@click.option(
    "--base",
    default="main",
    show_default=True,
    help="Base branch for diff comparison.",
)
@click.option(
    "--uncommitted",
    is_flag=True,
    help="Review staged and unstaged working tree changes.",
)
@click.option(
    "--pr",
    type=int,
    default=None,
    help="GitHub pull request number to review.",
)
@click.option(
    "--repo",
    default=None,
    help="GitHub repository (owner/name) when using --pr.",
)
@click.option(
    "--depth",
    type=click.IntRange(1, 3),
    default=None,
    help=(
        "Review depth (1=checklist, 2=+generated questions, 3=+adversarial). "
        "Defaults to review.depth in .lintro-config.yaml."
    ),
)
@click.option(
    "--strictness",
    type=click.Choice(
        [level.value for level in ReviewStrictness],
        case_sensitive=False,
    ),
    default=None,
    help=(
        "Review sensitivity preset: focused (merge blockers), balanced "
        "(default), thorough (hunt doc/migration nits in one pass)."
    ),
)
@click.option(
    "--semantic-chunks",
    is_flag=True,
    help=(
        "Split the diff into semantic chunks (slower; one agent call per chunk). "
        "Also enabled when review.force_semantic_chunking is true in config."
    ),
)
@click.option(
    "--show-checklist",
    type=click.Choice(["off", "linked", "all"], case_sensitive=False),
    default=None,
    help=(
        "Show structured checklist in output: linked (under findings), "
        "all (linked plus cleared/orphan appendices), or off to disable."
    ),
)
@click.option(
    "--post",
    is_flag=True,
    help="Post findings to GitHub as PR review comments.",
)
@click.option(
    "--output",
    "output_format",
    type=click.Choice(["terminal", "json"]),
    default="terminal",
    show_default=True,
    help="Output format for review results.",
)
@click.option(
    "--with-lint",
    is_flag=True,
    help="Run lintro tools on changed files and include results in review.",
)
@click.option(
    "--context-window",
    type=int,
    default=None,
    help="Override model context window size in tokens.",
)
@click.option(
    "--transport",
    type=click.Choice(["api", "cli"], case_sensitive=False),
    default=None,
    help="Override ai.transport for this invocation.",
)
@click.option(
    "--timeout",
    type=float,
    default=None,
    help="Override ai.api_timeout for this review (seconds).",
)
@click.option(
    "--path",
    "path_filter",
    multiple=True,
    help="Limit review to specific path prefixes.",
)
def review_command(
    *,
    base: str,
    uncommitted: bool,
    pr: int | None,
    repo: str | None,
    depth: int | None,
    strictness: str | None,
    semantic_chunks: bool,
    show_checklist: str | None,
    post: bool,
    output_format: str,
    with_lint: bool,
    context_window: int | None,
    timeout: float | None,
    path_filter: tuple[str, ...],
    transport: str | None,
) -> None:
    """Run AI-powered diff-based code review."""
    require_ai()
    lintro_config = get_config()
    if not lintro_config.ai.enabled:
        raise click.UsageError(
            "AI is disabled in configuration. Set ai.enabled: true in "
            ".lintro-config.yaml",
        )

    effective_repo = repo or os.environ.get("GITHUB_REPOSITORY")
    if pr is not None and not effective_repo:
        raise click.UsageError(
            "--pr requires --repo or GITHUB_REPOSITORY environment variable.",
        )
    if pr is not None and uncommitted:
        raise click.UsageError(
            "--pr and --uncommitted cannot be used together.",
        )
    resolved_pr: int | None = None
    if post:
        resolved_pr = pr or _detect_pr_number_from_env()
        if resolved_pr is None:
            raise click.UsageError(
                "--post requires --pr or a CI pull-request environment.",
            )
        if not effective_repo:
            raise click.UsageError(
                "--post requires --repo or GITHUB_REPOSITORY environment variable.",
            )

    paths = list(path_filter) if path_filter else None
    try:
        context = collect_review_context(
            base=base,
            uncommitted=uncommitted,
            pr_number=pr,
            repo=effective_repo,
            paths=paths,
        )
    except ReviewContextError as exc:
        raise click.ClickException(str(exc)) from exc

    classifications = classify_changed_files(context.changed_files)
    checklist_items = get_all_checklist_items(config=lintro_config)
    selected_items = select_checklist_items(
        classifications=classifications,
        items=checklist_items,
    )
    checklist_text, _prompt_mapping = format_checklist_for_prompt(
        items=selected_items,
    )
    question_map = build_prompt_question_map(items=selected_items)
    checklist_display = resolve_checklist_display(
        cli_value=show_checklist,
        config_value=lintro_config.review.checklist_display,
    )

    lint_digest: str | None = None
    if with_lint:
        from lintro.ai.review.lint_bridge import (
            format_lint_results_for_prompt,
            run_lint_on_changed_files,
        )

        lint_results = run_lint_on_changed_files(
            changed_files=[file.path for file in context.changed_files],
            lintro_config=lintro_config,
        )
        lint_digest = format_lint_results_for_prompt(results=lint_results)
        if lint_digest and output_format == "terminal":
            issue_count = sum(result.issues_count or 0 for result in lint_results)
            logger.info(
                "Ran lint on changed files: {} tools, {} issues",
                len(lint_results),
                issue_count,
            )

    effective_ai_config = apply_transport_override(lintro_config.ai, transport)
    if timeout is not None:
        effective_ai_config = effective_ai_config.model_copy(
            update={"api_timeout": timeout},
        )

    provider = get_provider(effective_ai_config)
    effective_depth = depth if depth is not None else lintro_config.review.depth
    effective_strictness = ReviewStrictness(
        (strictness or lintro_config.review.strictness.value).lower(),
    )
    sensitivity = resolve_sensitivity_policy(
        strictness=effective_strictness,
        overrides=lintro_config.review.sensitivity,
    )
    force_semantic_chunking = (
        semantic_chunks or lintro_config.review.force_semantic_chunking
    )

    progress_tracker = None
    console = Console()
    if output_format == "terminal":
        from lintro.ai.review.progress import RichReviewProgress

        progress_tracker = RichReviewProgress(console=console)

    try:
        result = run_review(
            context,
            provider=provider,
            ai_config=effective_ai_config,
            depth=effective_depth,
            checklist_items=selected_items,
            checklist_text=checklist_text,
            classifications=classifications,
            context_window_override=context_window,
            lint_results=lint_digest,
            progress=progress_tracker,
            sensitivity=sensitivity,
            force_semantic_chunking=force_semantic_chunking,
        )
    except (AIError, ValueError) as exc:
        if output_format == "json":
            raise click.ClickException(str(exc)) from exc
        render_review_error(error=exc, console=console)
        raise SystemExit(1) from exc

    result = enrich_review_result(result=result, question_map=question_map)

    output = render_review_output(
        result=result,
        output_format=output_format,
        checklist_display=checklist_display,
        question_map=question_map,
    )
    if output is not None:
        click.echo(output)

    if post:
        from lintro.ai.review.github import post_review_to_github

        posted = post_review_to_github(
            result=result,
            pr_number=resolved_pr,
            repo=effective_repo,
            checklist_display=checklist_display,
            question_map=question_map,
        )
        if not posted:
            logger.warning("GitHub review posting skipped or failed")

    exit_code = 1 if result.has_p1_findings else 0
    raise SystemExit(exit_code)


def _detect_pr_number_from_env() -> int | None:
    """Detect PR number from common CI environment variables."""
    github_ref = os.environ.get("GITHUB_REF", "")
    if github_ref.startswith("refs/pull/"):
        parts = github_ref.split("/")
        if len(parts) >= 3 and parts[2].isdigit():
            return int(parts[2])
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        return None
    try:
        import json
        from pathlib import Path

        payload = json.loads(Path(event_path).read_text(encoding="utf-8"))
        number = payload.get("pull_request", {}).get("number")
        return int(number) if isinstance(number, int) else None
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None

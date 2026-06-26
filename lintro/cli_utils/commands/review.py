"""CLI command for AI diff-based code review."""

from __future__ import annotations

import os

import click
from loguru import logger

from lintro.ai.availability import require_ai
from lintro.ai.providers import get_provider
from lintro.ai.review import (
    classify_changed_files,
    collect_review_context,
    format_checklist_for_prompt,
    get_all_checklist_items,
    select_checklist_items,
)
from lintro.ai.review.exceptions import ReviewContextError
from lintro.ai.review.orchestrator import run_review
from lintro.ai.review.output import render_review_output
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
    default=1,
    show_default=True,
    help="Review depth (1=checklist, 2=+generated questions, 3=+adversarial).",
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
    depth: int,
    post: bool,
    output_format: str,
    with_lint: bool,
    context_window: int | None,
    path_filter: tuple[str, ...],
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
    if post:
        resolved_pr = pr or _detect_pr_number_from_env()
        if resolved_pr is None:
            raise click.UsageError(
                "--post requires --pr or a CI pull-request environment.",
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
        changed_files=[file.path for file in context.changed_files],
        items=checklist_items,
    )
    checklist_text, _prompt_mapping = format_checklist_for_prompt(
        items=selected_items,
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

    provider = get_provider(lintro_config.ai)

    progress_tracker = None
    if output_format == "terminal":
        from lintro.ai.review.progress import RichReviewProgress

        progress_tracker = RichReviewProgress()

    result = run_review(
        context,
        provider=provider,
        ai_config=lintro_config.ai,
        depth=depth,
        checklist_items=selected_items,
        checklist_text=checklist_text,
        classifications=classifications,
        context_window_override=context_window,
        lint_results=lint_digest,
        progress=progress_tracker,
    )

    output = render_review_output(result=result, output_format=output_format)
    if output is not None:
        click.echo(output)

    if post:
        from lintro.ai.review.github import post_review_to_github

        posted = post_review_to_github(
            result=result,
            pr_number=pr or _detect_pr_number_from_env(),
            repo=effective_repo,
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

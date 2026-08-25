"""CLI command for AI code review.

``lintro review`` owns both AI review surfaces:

* the diff-based checklist review (epic #1609), and
* **advisory AI-finder tools** — plugins classified
  :attr:`~lintro.enums.execution_class.ExecutionClass.ADVISORY`, such as
  ``idiom-review``. Those used to run under ``lintro chk``; because their
  findings are nondeterministic opinions rather than rule violations they
  moved here, so ``chk`` stays deterministic and its health score stays
  stable across identical runs (#1308).

Advisory findings are scoped to the review's changed files (or ``--path``)
and never change the exit code unless ``--fail-on-findings`` is passed.
An advisory tool that failed to run fails ``--advisory-only``; a full
review still renders the completed review and records the advisory
failure in the advisory payload.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn

import click
from loguru import logger
from rich.console import Console

from lintro.ai.availability import require_ai
from lintro.ai.config import AIConfig
from lintro.ai.exceptions import (
    AIConfigOverrideError,
    AIError,
    AIProviderRequiredError,
)
from lintro.ai.paths import resolve_workspace_root
from lintro.ai.provider_enum import AIProvider, provider_required_error
from lintro.ai.providers import get_provider
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
from lintro.ai.review.cost_cap import cap_is_enforced
from lintro.ai.review.custom_agents import (
    CustomAgentSpec,
    discover_custom_agents,
    format_custom_agent_listing,
)
from lintro.ai.review.enums.changed_file_status import ChangedFileStatus
from lintro.ai.review.enums.custom_agent_mode import CustomAgentMode
from lintro.ai.review.enums.review_strictness import ReviewStrictness
from lintro.ai.review.error_display import render_review_error
from lintro.ai.review.exceptions import ReviewContextError
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.orchestrator import run_review
from lintro.ai.review.output import render_review_output
from lintro.ai.review.sensitivity import resolve_sensitivity_policy
from lintro.ai.review.state_store import (
    load_ci_state,
    load_local_state,
    local_ledger_key,
    migrate_legacy_sticky,
    state_dir,
    write_local_state,
    write_state_part,
)
from lintro.ai.transport import (
    apply_cli_overrides,
    apply_resolved_transport,
    format_resolved_profile_log,
    resolve_max_cost_with_source,
    resolve_transport_settings,
)
from lintro.config.config_loader import get_config
from lintro.enums.advisory_tools_value import AdvisoryToolsValue
from lintro.utils.execution.advisory import (
    ADVISORY_ERROR_METADATA_KEY,
    ADVISORY_ERROR_PROVIDER_REQUIRED,
    advisory_findings_count,
    advisory_results_to_payload,
    advisory_tools_errored,
    render_advisory_results,
    resolve_advisory_tools,
    run_advisory_tools,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from lintro.models.core.tool_result import ToolResult

#: Default paths scanned by ``--advisory-only`` when no ``--path`` is given.
ADVISORY_DEFAULT_PATHS: tuple[str, ...] = (".",)


def _fail_review_command(
    exc: AIError | ValueError,
    *,
    output_format: str,
    provider_label: str,
    post: bool,
    resolved_pr: int | None,
    effective_repo: str | None,
    console: Console | None = None,
    prior_state: ReviewState | None = None,
) -> NoReturn:
    """Render a review failure and exit with the review-error contract.

    Used for both early provider validation and mid-run provider failures so
    JSON, terminal, and GitHub error rendering stay on one path.

    Args:
        exc: The failure that prevented a review from running.
        output_format: CLI ``--output`` value (``json`` or ``terminal``).
        provider_label: Provider name, or ``unset`` when construction failed.
        post: Whether ``--post`` requested a GitHub comment.
        resolved_pr: PR number when posting is requested.
        effective_repo: ``owner/repo`` when posting is requested.
        console: Terminal console; created on demand for non-JSON output.
        prior_state: Prior resume state forwarded to the error sticky.

    Raises:
        SystemExit: Always, with the review-error exit code.
    """
    if post and resolved_pr is not None and effective_repo:
        from lintro.ai.review.github import post_review_error_to_github

        with suppress(Exception):
            post_review_error_to_github(
                error=exc,
                provider=provider_label,
                pr_number=resolved_pr,
                repo=effective_repo,
                prior_state=prior_state,
            )
    from lintro.ai.review.error_contract import (
        REVIEW_ERROR_EXIT_CODE,
        render_error_contract_json,
    )

    if output_format == "json":
        click.echo(
            render_error_contract_json(
                provider=provider_label,
                error=exc,
            ),
        )
    else:
        render_review_error(
            error=exc,
            console=console if console is not None else Console(),
        )
    # Same exit code in both output formats: no review was produced, which
    # must never be confusable with "reviewed, found P1 issues" (exit 1).
    # A wrapper that cannot tell the two apart reports a green check for a
    # review that never ran (#1826).
    raise SystemExit(REVIEW_ERROR_EXIT_CODE) from exc


def _advisory_failure_error(results: list[ToolResult]) -> AIError:
    """Build the exception for an advisory tool that failed to run.

    Args:
        results: Advisory tool results that include at least one error.

    Returns:
        ``AIProviderRequiredError`` when the failure is a missing provider,
        otherwise a generic ``AIError`` with the tool's output.
    """
    from lintro.enums.tool_run_status import ToolRunStatus, tool_run_status

    failed = next(
        result
        for result in results
        if tool_run_status(
            result=result,
            issue_count=result.issues_count or 0,
        )
        in {ToolRunStatus.ERRORED, ToolRunStatus.TIMED_OUT}
    )
    message = failed.output or f"{failed.name} failed"
    metadata = failed.metadata or {}
    if metadata.get(ADVISORY_ERROR_METADATA_KEY) == (ADVISORY_ERROR_PROVIDER_REQUIRED):
        return AIProviderRequiredError(message)
    return AIError(message)


@click.command("review")
@click.option(
    "--base",
    default=None,
    help=(
        "Base branch for diff comparison. When omitted, uses the repository "
        "default branch (origin/HEAD)."
    ),
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
    "--provider",
    "provider_override",
    type=click.Choice(
        [member.value for member in AIProvider],
        case_sensitive=False,
    ),
    default=None,
    help="Override ai.provider for this invocation.",
)
@click.option(
    "--model",
    "model_override",
    default=None,
    help="Override ai.model for this invocation.",
)
@click.option(
    "--review/--no-review",
    "review_override",
    default=None,
    help="Override ai.review for this invocation.",
)
@click.option(
    "--max-cost-usd",
    "max_cost_usd_override",
    default=None,
    help=(
        "Override ai.max_cost_usd for this invocation. A positive number is "
        "the USD cap; 'uncapped' lifts the ceiling. 0 is rejected as "
        "ambiguous."
    ),
)
@click.option(
    "--full",
    "force_full",
    is_flag=True,
    default=False,
    help="Discard carried coverage and review every eligible file again.",
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
@click.option(
    "--list-agents",
    is_flag=True,
    help=(
        "List user-defined review agents discovered in "
        ".lintro/review-agents/*.md and exit."
    ),
)
@click.option(
    "--advisory-tools",
    default=None,
    metavar="TOOLS",
    help=(
        "Advisory AI-finder tools to run (comma-separated), 'all' for every "
        "enabled advisory tool (default), or 'none' to skip them. Advisory "
        "tools do not run under 'lintro chk'."
    ),
)
@click.option(
    "--tool-options",
    "tool_options",
    default=None,
    help=(
        "Options for advisory tools, as tool:option=value[,tool:option=value] "
        "(e.g. idiom-review:enabled=true)."
    ),
)
@click.option(
    "--advisory-only",
    is_flag=True,
    help=(
        "Run only the advisory AI-finder tools and skip the diff-based "
        "review. Scans --path values, or the current directory."
    ),
)
@click.option(
    "--fail-on-findings",
    is_flag=True,
    help=(
        "Exit 1 when advisory tools report findings "
        "(default: advisory findings exit 0)."
    ),
)
def review_command(
    *,
    base: str | None,
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
    provider_override: str | None,
    model_override: str | None,
    review_override: bool | None,
    max_cost_usd_override: str | None,
    force_full: bool,
    list_agents: bool,
    advisory_tools: str | None,
    tool_options: str | None,
    advisory_only: bool,
    fail_on_findings: bool,
) -> None:
    """Run AI-powered diff-based code review, plus advisory AI finders."""
    lintro_config = get_config()
    workspace_root = resolve_workspace_root(lintro_config.config_path)
    if list_agents:
        # Listing is config inspection only: no provider and no AI credentials
        # are needed to answer "which agents would run?".
        click.echo(
            format_custom_agent_listing(
                discovery=discover_custom_agents(workspace_root=workspace_root),
                mode=lintro_config.review.custom_agents,
            ),
        )
        raise SystemExit(0)

    if advisory_only and (post or pr is not None or uncommitted or base):
        raise click.UsageError(
            "--advisory-only runs advisory tools over paths and produces no "
            "diff review, so it cannot be combined with --base, "
            "--uncommitted, --pr or --post.",
        )

    require_ai()
    try:
        resolved_ai = apply_cli_overrides(
            AIConfig.resolve_from_mapping(lintro_config.ai),
            provider=provider_override,
            model=model_override,
            transport=transport,
            review=review_override,
            max_cost_usd=max_cost_usd_override,
        )
    except AIConfigOverrideError as exc:
        raise click.UsageError(str(exc)) from exc
    ai_config = resolved_ai.config
    if advisory_only:
        # Advisory-only needs no diff context and no review checklist, so it
        # deliberately bypasses the ai.review gate: the user asked for the
        # finder tools, not the diff review (#1308). --provider still applies.
        if ai_config.provider is None:
            _fail_review_command(
                AIProviderRequiredError(provider_required_error()),
                output_format=output_format,
                provider_label="unset",
                post=False,
                resolved_pr=None,
                effective_repo=None,
            )
        _run_advisory_only(
            advisory_tools=advisory_tools,
            tool_options=tool_options,
            path_filter=path_filter,
            output_format=output_format,
            fail_on_findings=fail_on_findings,
            provider_label=(
                ai_config.provider.value if ai_config.provider is not None else "unset"
            ),
            ai_config=ai_config,
        )

    if not ai_config.review_enabled:
        raise click.UsageError(
            "AI review is disabled. Set ai.review: true, LINTRO_AI_REVIEW=1, "
            "or pass --review (and enable ai.enabled via config or "
            "LINTRO_AI_ENABLED=1).",
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
    if pr is None and repo is not None and not post:
        raise click.UsageError("--repo can only be used with --pr.")
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

    # Fail on a missing provider before git/gh work so an invalid base or a
    # non-repo cwd cannot hide the required-provider migration error.
    if ai_config.provider is None:
        _fail_review_command(
            AIProviderRequiredError(provider_required_error()),
            output_format=output_format,
            provider_label="unset",
            post=post,
            resolved_pr=resolved_pr,
            effective_repo=effective_repo,
        )

    paths = list(path_filter) if path_filter else None
    context_pr = resolved_pr if post else pr
    context_repo = effective_repo if context_pr is not None else None
    context_started = time.monotonic()
    try:
        context = collect_review_context(
            base=base,
            uncommitted=uncommitted,
            pr_number=context_pr,
            repo=context_repo,
            paths=paths,
            exclude_globs=list(ai_config.exclude_paths),
        )
    except ReviewContextError as exc:
        raise click.ClickException(str(exc)) from exc
    context_collection_seconds = time.monotonic() - context_started

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

    effective_ai_config = ai_config
    if timeout is not None:
        effective_ai_config = effective_ai_config.model_copy(
            update={"api_timeout": timeout},
        )
        # Explicit --timeout wins over the transport profile for this run.
        if effective_ai_config.transport is not None:
            transports = effective_ai_config.transports.model_copy(deep=True)
            if effective_ai_config.transport.value == "cli":
                transports.cli.timeout = timeout
            else:
                transports.api.timeout = timeout
            effective_ai_config = effective_ai_config.model_copy(
                update={"transports": transports},
            )
    effective_ai_config = apply_resolved_transport(effective_ai_config)
    resolved_profile = resolve_transport_settings(effective_ai_config)
    logger.info(
        "AI review transport profile: {}",
        format_resolved_profile_log(resolved_profile),
    )

    provider = None
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
    custom_agent_mode = lintro_config.review.custom_agents
    custom_agents = _resolve_custom_agents(
        mode=custom_agent_mode,
        workspace_root=workspace_root,
    )

    progress_tracker = None
    console = Console()
    if output_format == "terminal":
        from lintro.ai.review.progress import RichReviewProgress

        progress_tracker = RichReviewProgress(console=console)

    cap, cap_source = resolve_max_cost_with_source(resolved_ai)
    enforce_cap = cap_is_enforced(
        source=cap_source,
        basis=resolved_profile.cost_basis,
    )
    prior_state = _load_prior_review_state(
        pr_number=pr,
        head_ref=context.head_ref,
        repo=effective_repo or os.environ.get("GITHUB_REPOSITORY", ""),
        post=post,
    )
    try:
        provider = get_provider(effective_ai_config, workspace_root=workspace_root)
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
            custom_agents=custom_agents,
            run_builtin_checklist=custom_agent_mode != CustomAgentMode.ONLY,
            workspace_root=workspace_root,
            context_collection_seconds=context_collection_seconds,
            prior_state=prior_state,
            force_full=force_full,
            enforce_cost_cap=enforce_cap,
        )
        from dataclasses import replace as dc_replace

        from lintro.ai.enums.cost_basis import CostBasis

        # The profile resolves BILLED for the api transport *before* the run;
        # when the provider returned no usage counters the orchestrator set
        # token_usage_estimated, so the honest post-run basis is ESTIMATED.
        # Stamping billed here would also suppress the legacy derivation in
        # github_sticky._run_record, which only fires on an empty basis.
        effective_basis = resolved_profile.cost_basis
        if result.metadata.token_usage_estimated and (
            effective_basis is CostBasis.BILLED
        ):
            effective_basis = CostBasis.ESTIMATED

        result = dc_replace(
            result,
            metadata=dc_replace(
                result.metadata,
                transport=resolved_profile.transport.value,
                auth_mode=resolved_profile.auth_mode,
                cost_basis=effective_basis.value,
                provider_source=resolved_ai.source_of("provider").value,
                model_source=resolved_ai.source_of("model").value,
                transport_source=resolved_ai.source_of("transport").value,
                max_cost_usd=cap,
                max_cost_usd_source=cap_source.value,
            ),
        )
        if post and prior_state is not None and not force_full:
            from lintro.ai.review.finding_matcher import (
                review_findings_from_unposted,
            )

            replayed = review_findings_from_unposted(
                prior=prior_state,
                current=result.findings,
                reviewed_paths=frozenset(result.metadata.reviewed_paths),
            )
            if replayed:
                result = dc_replace(
                    result,
                    findings=(*result.findings, *replayed),
                )
        try:
            _persist_review_state(
                result=result,
                context=context,
                prior=prior_state,
                force_full=force_full,
                pr_number=pr,
                repo=effective_repo or os.environ.get("GITHUB_REPOSITORY", ""),
            )
        except Exception:
            logger.warning(
                "Could not persist review-resume state; next round re-reviews",
            )
    except (AIError, ValueError) as exc:
        _fail_review_command(
            exc,
            output_format=output_format,
            provider_label=(str(provider.name) if provider is not None else "unset"),
            post=post,
            resolved_pr=resolved_pr,
            effective_repo=effective_repo,
            console=console,
            prior_state=prior_state,
        )

    result = enrich_review_result(result=result, question_map=question_map)

    advisory_results = _execute_advisory(
        advisory_tools=advisory_tools,
        tool_options=tool_options,
        paths=_existing_changed_files(
            changed_files=context.changed_files,
            workspace_root=workspace_root,
        ),
        ai_config=effective_ai_config,
    )

    output = render_review_output(
        result=result,
        output_format=output_format,
        checklist_display=checklist_display,
        question_map=question_map,
    )
    if output_format == "json":
        output = _merge_advisory_into_json(
            review_output=output,
            advisory_results=advisory_results,
        )
    if output is not None:
        click.echo(output)
    if output_format != "json":
        advisory_text = render_advisory_results(results=advisory_results)
        if advisory_text:
            click.echo(f"\n{advisory_text}")

    if post:
        from lintro.ai.review.github import post_review_to_github

        captured_comment_ids: dict[str, int] = {}
        posted = post_review_to_github(
            result=result,
            pr_number=resolved_pr,
            repo=effective_repo,
            prior_state=prior_state,
            departed_paths=_departed_paths(context=context),
            checklist_display=checklist_display,
            question_map=question_map,
            transport=resolved_profile.transport.value,
            auth_mode=resolved_profile.auth_mode,
            # metadata carries the post-run reconciled basis (estimated when
            # the provider reported no usage), not the pre-run profile value.
            cost_basis=result.metadata.cost_basis,
            auto_resolve=lintro_config.review.auto_resolve,
            config_source=_describe_config_source(
                config_path=lintro_config.config_path,
                overrides=_cli_overrides(
                    depth=depth,
                    strictness=strictness,
                    transport=transport,
                    provider=provider_override,
                    model=model_override,
                    review=review_override,
                    max_cost_usd=max_cost_usd_override,
                    timeout=timeout,
                    context_window=context_window,
                    semantic_chunks=semantic_chunks,
                    paths=paths,
                ),
            ),
            captured_comment_ids=captured_comment_ids,
        )
        if captured_comment_ids:
            try:
                _persist_review_state(
                    result=result,
                    context=context,
                    prior=prior_state,
                    force_full=force_full,
                    pr_number=pr,
                    repo=effective_repo or os.environ.get("GITHUB_REPOSITORY", ""),
                    inline_comment_ids=captured_comment_ids,
                )
            except Exception:
                logger.warning(
                    "Could not persist posted inline comment ids; next "
                    "round may replay those findings",
                )
        if not posted:
            logger.warning("GitHub review posting skipped or failed")

    exit_code = 1 if result.has_p1_findings else 0
    if fail_on_findings and advisory_findings_count(advisory_results):
        exit_code = 1
    raise SystemExit(exit_code)


def _cli_overrides(
    *,
    depth: int | None,
    strictness: str | None,
    transport: str | None,
    provider: str | None,
    model: str | None,
    review: bool | None,
    max_cost_usd: float | str | None,
    timeout: float | None,
    context_window: int | None,
    semantic_chunks: bool,
    paths: list[str] | None,
) -> list[str]:
    """List the CLI flags that overrode configured review settings.

    Only explicitly-passed options are listed: the point of the note is to
    explain why a posted run's stats differ from the checked-in config, so
    defaults would be noise.

    Args:
        depth: ``--depth`` value, or None when unset.
        strictness: ``--strictness`` value, or None when unset.
        transport: ``--transport`` value, or None when unset.
        provider: ``--provider`` value, or None when unset.
        model: ``--model`` value, or None when unset.
        review: ``--review/--no-review`` value, or None when unset.
        max_cost_usd: ``--max-cost-usd`` value, or None when unset.
        timeout: ``--timeout`` value, or None when unset.
        context_window: ``--context-window`` value, or None when unset.
        semantic_chunks: Whether ``--semantic-chunks`` was passed.
        paths: ``--path`` values, or None when unset.

    Returns:
        Rendered flag strings in CLI order.
    """
    overrides: list[str] = []
    if depth is not None:
        overrides.append(f"--depth {depth}")
    if strictness is not None:
        overrides.append(f"--strictness {strictness}")
    if transport is not None:
        overrides.append(f"--transport {transport}")
    if provider is not None:
        overrides.append(f"--provider {provider}")
    if model is not None:
        overrides.append(f"--model {model}")
    if review is not None:
        overrides.append("--review" if review else "--no-review")
    if max_cost_usd is not None:
        overrides.append(f"--max-cost-usd {max_cost_usd}")
    if timeout is not None:
        overrides.append(f"--timeout {timeout:g}")
    if context_window is not None:
        overrides.append(f"--context-window {context_window}")
    if semantic_chunks:
        overrides.append("--semantic-chunks")
    overrides.extend(f"--path {path}" for path in paths or [])
    return overrides


def _describe_config_source(
    *,
    config_path: str | None,
    overrides: list[str],
) -> str:
    """Describe where this run's settings came from.

    The config file is named, never pathed: an absolute path on a CI runner
    leaks the workspace layout into a public PR comment and tells the reader
    nothing they can act on.

    Args:
        config_path: Path of the loaded lintro config, if any.
        overrides: Rendered CLI override flags.

    Returns:
        Human-readable provenance string for the posted run stats.
    """
    base = f"`{Path(config_path).name}`" if config_path else "built-in defaults"
    if not overrides:
        return base
    return f"{base} + CLI overrides ({', '.join(overrides)})"


def _existing_changed_files(
    *,
    changed_files: Sequence[object],
    workspace_root: Path,
) -> list[str]:
    """Resolve a review context's changed files to on-disk absolute paths.

    Paths are resolved against the workspace root rather than the process cwd,
    and deleted or renamed-away files are dropped: an advisory tool must never
    be handed a path it cannot open.

    Args:
        changed_files: Changed-file entries carrying a ``path`` attribute.
        workspace_root: Absolute workspace root the paths are relative to.

    Returns:
        Absolute paths of the changed files that still exist.
    """
    paths: list[str] = []
    for changed in changed_files:
        raw = getattr(changed, "path", None)
        if not raw:
            continue
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = workspace_root / candidate
        if candidate.is_file():
            paths.append(str(candidate))
    return paths


def _execute_advisory(
    *,
    advisory_tools: str | None,
    tool_options: str | None,
    paths: list[str],
    ai_config: AIConfig | None = None,
) -> list[ToolResult]:
    """Resolve and run the advisory tools requested for this review.

    Args:
        advisory_tools: Raw ``--advisory-tools`` value.
        tool_options: Raw ``--tool-options`` value for advisory tools.
        paths: Paths to scan (typically the review's changed files).
        ai_config: CLI-resolved AI configuration forwarded to each tool.

    Returns:
        One result per advisory tool that ran; empty when none were selected.

    Raises:
        click.UsageError: If a requested advisory tool is unknown or is not
            an advisory tool.
    """
    try:
        selection = resolve_advisory_tools(requested=advisory_tools)
    except ValueError as exc:
        raise click.UsageError(str(exc)) from exc
    # Only report explicitly named tools; the default 'all' selection skipping
    # a config-disabled tool is the normal, quiet case.
    explicit_selection = (advisory_tools or "").strip().lower() not in (
        "",
        AdvisoryToolsValue.ALL,
    )
    if explicit_selection:
        for skipped in selection.skipped:
            logger.info(
                "Skipping advisory tool {}: {}",
                skipped.name,
                skipped.reason,
            )
    return run_advisory_tools(
        paths=paths,
        tool_names=selection.to_run,
        tool_options=tool_options,
        ai_config=ai_config,
    )


def _run_advisory_only(
    *,
    advisory_tools: str | None,
    tool_options: str | None,
    path_filter: tuple[str, ...],
    output_format: str,
    fail_on_findings: bool,
    provider_label: str,
    ai_config: AIConfig | None = None,
) -> None:
    """Run only the advisory tools, render their findings, and exit.

    Args:
        advisory_tools: Raw ``--advisory-tools`` value.
        tool_options: Raw ``--tool-options`` value for advisory tools.
        path_filter: ``--path`` values; defaults to the current directory.
        output_format: ``terminal`` or ``json``.
        fail_on_findings: Whether findings should produce exit code 1.
        provider_label: Resolved provider name for the error contract.
        ai_config: CLI-resolved AI configuration forwarded to each tool.

    Raises:
        SystemExit: Always; carries the resolved exit code.
        click.UsageError: If the selection resolves to no tools at all.
    """
    if (advisory_tools or "").strip().lower() == AdvisoryToolsValue.NONE:
        raise click.UsageError(
            "--advisory-only with --advisory-tools none would run nothing.",
        )
    paths = list(path_filter) if path_filter else list(ADVISORY_DEFAULT_PATHS)
    results = _execute_advisory(
        advisory_tools=advisory_tools,
        tool_options=tool_options,
        paths=paths,
        ai_config=ai_config,
    )
    if advisory_tools_errored(results):
        _fail_review_command(
            _advisory_failure_error(results),
            output_format=output_format,
            provider_label=provider_label,
            post=False,
            resolved_pr=None,
            effective_repo=None,
        )
    if output_format == "json":
        click.echo(
            json.dumps(
                {"advisory": advisory_results_to_payload(results)},
                indent=2,
            ),
        )
    else:
        click.echo(
            render_advisory_results(results=results) or "No advisory tools ran.",
        )
    findings = advisory_findings_count(results)
    raise SystemExit(1 if (fail_on_findings and findings) else 0)


def _merge_advisory_into_json(
    *,
    review_output: str | None,
    advisory_results: list[ToolResult],
) -> str | None:
    """Add an ``advisory`` key to the review's JSON document.

    The key is purely additive so existing consumers of the review JSON
    contract keep parsing unchanged documents.

    Args:
        review_output: Rendered review JSON, or ``None``.
        advisory_results: Advisory tool results to attach.

    Returns:
        The JSON document with an ``advisory`` key, or the original output
        when it is absent or not a JSON object.
    """
    if not advisory_results or not isinstance(review_output, str):
        return review_output
    try:
        document = json.loads(review_output)
    except json.JSONDecodeError:
        return review_output
    if not isinstance(document, dict):
        return review_output
    document["advisory"] = advisory_results_to_payload(advisory_results)
    return json.dumps(document, indent=2)


def _resolve_custom_agents(
    *,
    mode: CustomAgentMode,
    workspace_root: Path,
) -> tuple[CustomAgentSpec, ...]:
    """Discover user-defined review agents for the configured mode.

    Invalid agent files are reported as warnings and skipped so one malformed
    file never fails the review run.

    Args:
        mode: Configured ``review.custom_agents`` mode.
        workspace_root: Absolute workspace root to scan.

    Returns:
        The discovered agents, or an empty tuple when discovery is disabled.

    Raises:
        click.UsageError: When ``mode`` is ``only`` and no valid agents were
            discovered, since the built-in checklist is skipped in that mode
            and running would silently review nothing.
    """
    if mode == CustomAgentMode.DISABLED:
        return ()
    discovery = discover_custom_agents(workspace_root=workspace_root)
    for issue in discovery.issues:
        logger.warning("Skipping invalid review agent — {issue}", issue=issue.format())
    if mode == CustomAgentMode.ONLY and not discovery.agents:
        raise click.UsageError(
            "review.custom_agents is 'only' but no valid agents were found "
            f"in {discovery.directory}; the built-in checklist is skipped in "
            "'only' mode, so there is nothing left to review. Add a valid "
            "agent file or change review.custom_agents.",
        )
    return discovery.agents


def _load_prior_review_state(
    *,
    pr_number: int | None,
    head_ref: str,
    repo: str,
    post: bool,
) -> ReviewState:
    """Load CI artifact, local ledger, or a one-time sticky migration."""
    if os.environ.get("GITHUB_ACTIONS") == "true":
        return load_ci_state(
            directory=state_dir(ci=True),
            repo=repo,
            pr_number=pr_number or 0,
        )
    key = local_ledger_key(pr_number=pr_number, head_ref=head_ref)
    local = load_local_state(
        key=key,
        repo=repo,
        pr_number=pr_number,
    )
    if local.coverage or local.runs or local.findings:
        return local
    if post:
        migrated = _migrate_sticky_state(pr_number=pr_number, repo=repo)
        if migrated.runs or migrated.findings:
            return migrated
    return ReviewState()


def _migrate_sticky_state(*, pr_number: int | None, repo: str) -> ReviewState:
    """Seed findings and runs from a legacy v2 sticky blob, never coverage."""
    with suppress(Exception):
        from lintro.ai.integrations.github_pr import GitHubPRReporter
        from lintro.ai.review.github_constants import STICKY_MARKER

        reporter = GitHubPRReporter(pr_number=pr_number, repo=repo or None)
        found = reporter.find_issue_comment(marker=STICKY_MARKER)
        if found is None:
            return ReviewState()
        return migrate_legacy_sticky(body=found[1])
    return ReviewState()


def _persist_review_state(
    *,
    result: object,
    context: object,
    prior: ReviewState | None,
    force_full: bool,
    pr_number: int | None,
    repo: str,
    inline_comment_ids: dict[str, int] | None = None,
) -> None:
    """Write coverage parts for the artifact upload and local ledger."""
    from dataclasses import replace
    from importlib.metadata import version as pkg_version

    from lintro.ai.review.github_sticky import advance_review_state
    from lintro.ai.review.models.review_result import ReviewResult

    del force_full
    if not isinstance(result, ReviewResult):
        return
    advanced = advance_review_state(
        result=result,
        prior_state=prior,
        head_sha=str(getattr(context, "head_ref", "") or ""),
        transport=result.metadata.transport,
        auth_mode=result.metadata.auth_mode,
        cost_basis=result.metadata.cost_basis,
        inline_comment_ids=inline_comment_ids,
        departed_paths=_departed_paths(context=context),
    )
    state = replace(
        advanced,
        repo=repo,
        pr_number=pr_number,
        base_sha=str(getattr(context, "base_ref", "") or ""),
        head_sha=str(getattr(context, "head_ref", "") or ""),
        workflow="ai-review.yml",
        event=os.environ.get("GITHUB_EVENT_NAME", ""),
        run_id=os.environ.get("GITHUB_RUN_ID", ""),
        lintro_version=_lintro_version(pkg_version),
    )
    directory = state_dir(ci=os.environ.get("GITHUB_ACTIONS") == "true")
    write_state_part(
        state=state,
        directory=directory,
        sequence=1,
        final=True,
    )
    if os.environ.get("GITHUB_ACTIONS") != "true":
        write_local_state(
            state=state,
            key=local_ledger_key(
                pr_number=pr_number,
                head_ref=str(getattr(context, "head_ref", "") or ""),
            ),
        )


def _departed_paths(*, context: object) -> frozenset[str]:
    """Return paths that left the diff (deletes and rename sources)."""
    changed = getattr(context, "changed_files", ())
    departed: set[str] = set()
    for item in changed:
        status = item.status
        if not isinstance(status, ChangedFileStatus):
            try:
                status = ChangedFileStatus(str(status))
            except ValueError:
                continue
        if status is ChangedFileStatus.DELETED:
            departed.add(item.path)
        previous = getattr(item, "previous_path", None)
        if previous and status is ChangedFileStatus.RENAMED:
            departed.add(previous)
    return frozenset(departed)


def _lintro_version(pkg_version: Callable[[str], str]) -> str:
    """Return the installed lintro version, or empty."""
    try:
        return str(pkg_version("lintro"))
    except Exception:
        return ""


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
        payload = json.loads(Path(event_path).read_text(encoding="utf-8"))
        number = payload.get("pull_request", {}).get("number")
        return int(number) if isinstance(number, int) else None
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None

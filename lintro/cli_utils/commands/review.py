"""CLI command for AI code review.

``lintro review`` owns both AI review surfaces:

* the diff-based checklist review (epic #1609), and
* **advisory AI-finder tools** — plugins classified
  :attr:`~lintro.enums.execution_class.ExecutionClass.ADVISORY`, such as
  ``idiom-review``. Those used to run under ``lintro chk``; because their
  findings are nondeterministic opinions rather than rule violations they
  moved here, so ``chk`` stays deterministic and its issue counts stay
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
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn

import click
from loguru import logger
from rich.console import Console

from lintro.ai.availability import require_ai
from lintro.ai.config import AIConfig
from lintro.ai.effective_config import (
    AICliOverrides,
    resolve_effective_ai_config,
)
from lintro.ai.exceptions import (
    AIConfigOverrideError,
    AIError,
    AIProviderRequiredError,
)
from lintro.ai.paths import resolve_workspace_root
from lintro.ai.provider_enum import AIProvider, provider_required_error
from lintro.ai.providers import get_provider
from lintro.ai.review.checklist_display import (
    build_prompt_question_map,
    enrich_review_result,
    resolve_checklist_display,
)
from lintro.ai.review.convergence import (
    evaluate_convergence,
    format_convergence_stamp,
    format_trajectory,
)
from lintro.ai.review.cost_cap import cap_is_enforced
from lintro.ai.review.custom_agents import (
    discover_custom_agents,
    format_custom_agent_listing,
)
from lintro.ai.review.enums.changed_file_status import ChangedFileStatus
from lintro.ai.review.enums.review_strictness import ReviewStrictness
from lintro.ai.review.error_display import render_review_error
from lintro.ai.review.exceptions import ReviewContextError, ReviewPreparationError
from lintro.ai.review.finding_matcher import count_blocking_findings
from lintro.ai.review.models.convergence_decision import ConvergenceDecision
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.orchestrator import guard_changed_paths
from lintro.ai.review.output import (
    render_convergence_outcome_json,
    render_review_output,
)
from lintro.ai.review.patch_validation import validate_result_suggested_patches
from lintro.ai.review.preparation import (
    PreparedReview,
    ReviewExecutionPolicy,
    ReviewRunRequest,
    execute_review,
    prepare_review,
)
from lintro.ai.review.severity_gate import apply_cross_chunk_guard
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

    from lintro.ai.enums import ConfigSource
    from lintro.ai.resolved_ai_config import ResolvedAIConfig
    from lintro.ai.review.models.review_context import ReviewContext
    from lintro.ai.review.models.review_result import ReviewResult
    from lintro.ai.transport import ResolvedTransportSettings
    from lintro.config.lintro_config import LintroConfig
    from lintro.enums.checklist_display import ChecklistDisplay
    from lintro.models.core.tool_result import ToolResult


#: Default paths scanned by ``--advisory-only`` when no ``--path`` is given.
ADVISORY_DEFAULT_PATHS: tuple[str, ...] = (".",)


@dataclass(frozen=True, slots=True)
class ReviewCommandOptions:
    """Every ``lintro review`` option, exactly as Click parsed it.

    Click populates this from the decorators on :func:`review_command`, so the
    command takes one typed argument rather than twenty-five (#2313 pattern).
    The defaults mirror the option declarations and exist so tests and helpers
    can build a partial option set; production always passes all of them.

    Attributes:
        base: ``--base`` value, or None for the default branch.
        uncommitted: Whether ``--uncommitted`` was passed.
        pr: ``--pr`` value, or None.
        repo: ``--repo`` value, or None.
        depth: ``--depth`` value, or None for ``review.depth``.
        strictness: ``--strictness`` value, or None for ``review.strictness``.
        semantic_chunks: Whether ``--semantic-chunks`` was passed.
        show_checklist: ``--show-checklist`` value, or None for config.
        post: Whether ``--post`` was passed.
        output_format: ``--output`` value (``terminal`` or ``json``).
        with_lint: Whether ``--with-lint`` was passed.
        context_window: ``--context-window`` value, or None.
        timeout: ``--timeout`` value in seconds, or None.
        path_filter: ``--path`` values.
        transport: ``--transport`` value, or None.
        provider_override: ``--provider`` value, or None.
        model_override: ``--model`` value, or None.
        review_override: ``--review/--no-review`` value, or None.
        max_cost_usd_override: ``--max-cost-usd`` value, or None.
        force_full: Whether ``--full`` was passed.
        list_agents: Whether ``--list-agents`` was passed.
        advisory_tools: ``--advisory-tools`` value, or None.
        tool_options: ``--tool-options`` value, or None.
        advisory_only: Whether ``--advisory-only`` was passed.
        fail_on_findings: Whether ``--fail-on-findings`` was passed.
    """

    base: str | None = None
    uncommitted: bool = False
    pr: int | None = None
    repo: str | None = None
    depth: int | None = None
    strictness: str | None = None
    semantic_chunks: bool = False
    show_checklist: str | None = None
    post: bool = False
    output_format: str = "terminal"
    with_lint: bool = False
    context_window: int | None = None
    timeout: float | None = None
    path_filter: tuple[str, ...] = ()
    transport: str | None = None
    provider_override: str | None = None
    model_override: str | None = None
    review_override: bool | None = None
    max_cost_usd_override: str | None = None
    force_full: bool = False
    list_agents: bool = False
    advisory_tools: str | None = None
    tool_options: str | None = None
    advisory_only: bool = False
    fail_on_findings: bool = False


@dataclass(frozen=True, slots=True)
class _ReviewTargets:
    """The GitHub repository and pull request one review run acts on.

    Attributes:
        effective_repo: ``owner/repo`` from ``--repo`` or the environment.
        resolved_pr: The PR ``--post`` will comment on, or None.
        state_pr: The PR whose resume state this run reads and writes.
    """

    effective_repo: str | None
    resolved_pr: int | None
    state_pr: int | None


@dataclass(frozen=True, slots=True)
class _MetadataStamp:
    """Run provenance stamped onto the completed review's metadata.

    Attributes:
        profile: The resolved transport profile the run used.
        resolved_ai: Effective AI configuration with per-field provenance.
        cap: The effective spend ceiling, or None when uncapped.
        cap_source: Where that ceiling came from.
    """

    profile: ResolvedTransportSettings
    resolved_ai: ResolvedAIConfig
    cap: float | None
    cap_source: ConfigSource


@dataclass(frozen=True, slots=True)
class _ReviewRender:
    """How a completed review's checklist is rendered and attributed.

    Attributes:
        checklist_display: Resolved ``--show-checklist`` mode.
        question_map: Prompt checklist id to question text, used to attribute
            findings back to the checklist they answered.
    """

    checklist_display: ChecklistDisplay
    question_map: dict[int, str]


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


def _finish_converged_review(
    *,
    decision: ConvergenceDecision,
    output_format: str,
    post: bool,
    resolved_pr: int | None,
    effective_repo: str | None,
    prior_state: ReviewState,
) -> NoReturn:
    """Stamp a short-circuited round and exit without calling the provider.

    Reached only when the convergence stop rule fired (#2099), which happens
    before the provider is constructed — so no provider call is made and the
    coverage and resume bookkeeping are never touched. Deliberately no state
    is persisted: no round ran, so the round counter, the tracked findings,
    and the carried coverage all stay exactly as the last real round left
    them, and the next round that *does* run resumes from there untouched.

    This raises, so it short-circuits the *whole* command, not just the review
    round: the advisory-tool tail and the ``--fail-on-findings`` gate below
    never run either. That is the point — the skip exists to spend nothing —
    and ``--full`` re-runs the command end to end when the advisory tools are
    wanted. Context collection and ``--with-lint`` have already run by this
    point, so "costs nothing" means no provider call, not literally no work.

    The process exit contract is the same one a real round uses:
    :func:`~lintro.ai.review.finding_matcher.count_blocking_findings` — open,
    non-question P1s — mirroring ``ReviewResult.has_p1_findings`` and
    :func:`~lintro.ai.review.finding_matcher.derive_verdict`, which share that
    predicate. Questions never block.

    That exit is a local signal only. The CI check does *not* redden for open
    P1s on either path: ``scripts/ci/classify_review_outcome.py`` reports a
    REVIEWED round's P1 findings and a converged skip's leftovers alike, and
    exits 0 for both (see the exit-code contract in
    ``scripts/ci/run-ai-review.sh``). The readiness gate is informational at
    check level, so the count is surfaced — on the sticky banner, in the JSON
    envelope's ``open_p1``, and in the classifier headline — rather than
    being hidden behind an exit code that would make a skip stricter than the
    round that found the findings.

    Args:
        decision: The converged decision that skipped the round.
        output_format: CLI ``--output`` value (``json`` or ``terminal``).
        post: Whether ``--post`` requested a GitHub comment.
        resolved_pr: PR number when posting is requested.
        effective_repo: ``owner/repo`` when posting is requested.
        prior_state: State already loaded for this invocation, re-rendered as
            the board the banner is stamped onto.

    Raises:
        SystemExit: Always. ``0`` for a clean skip; ``1`` when the last real
            round left an open P1 — the same local exit a round that found
            them produces, and equally not a CI failure.
    """
    if post and resolved_pr is not None and effective_repo:
        from lintro.ai.review.github import post_review_converged_to_github

        with suppress(Exception):
            post_review_converged_to_github(
                decision=decision,
                pr_number=resolved_pr,
                repo=effective_repo,
                prior_state=prior_state,
            )
    # A skipped round changes nothing about what is open, so it reports the
    # same local exit a real round would: an open P1 left by the last real
    # round still exits 1 here, exactly as that round did. The CI check
    # greens both alike and names the count instead (see the docstring).
    open_p1 = count_blocking_findings(findings=prior_state.findings)
    if output_format == "json":
        click.echo(
            render_convergence_outcome_json(decision=decision, open_p1=open_p1),
        )
    else:
        click.echo(f"🔁 Review skipped — {format_convergence_stamp(decision=decision)}")
        if decision.trajectory:
            click.echo(
                f"   Score trajectory: {format_trajectory(scores=decision.trajectory)}",
            )
        if open_p1:
            click.echo(
                f"   {open_p1} open P1 finding(s) from the last round still "
                "block: exiting 1.",
            )
        click.echo("   Re-run with --full to force another round.")
    raise SystemExit(1 if open_p1 else 0)


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
        "ambiguous; an empty value is treated as unset."
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
def review_command(**click_options: Any) -> None:
    """Run AI-powered diff-based code review, plus advisory AI finders.

    \u000c

    Args:
        **click_options: Every declared option, as Click passes it. They are
            collected straight into :class:`ReviewCommandOptions` so the
            command has one typed argument instead of twenty-five positional
            ones (#2313 pattern); the decorators above remain the single
            source of truth for names and defaults.
    """
    _review(options=ReviewCommandOptions(**click_options))


def _review(*, options: ReviewCommandOptions) -> None:
    """Run ``lintro review`` for one fully-populated option set.

    Parse → request → prepare → execute → render/post/exit. Preparation and
    execution belong to :mod:`lintro.ai.review.preparation`, which the MCP
    surface calls too — from its own envelope, so its request omits the
    CLI-only fields. Everything in this module is CLI policy (#2300).

    Args:
        options: The command's Click-populated options.

    Raises:
        SystemExit: Always, once the review or the advisory run has produced
            its outcome.
        click.UsageError: When the option combination cannot describe a review.
    """
    lintro_config = get_config()
    workspace_root = resolve_workspace_root(lintro_config.config_path)
    if options.list_agents:
        # Listing is config inspection only: no provider and no AI credentials
        # are needed to answer "which agents would run?".
        click.echo(
            format_custom_agent_listing(
                discovery=discover_custom_agents(workspace_root=workspace_root),
                mode=lintro_config.review.custom_agents,
            ),
        )
        raise SystemExit(0)

    if options.advisory_only and (
        options.post or options.pr is not None or options.uncommitted or options.base
    ):
        raise click.UsageError(
            "--advisory-only runs advisory tools over paths and produces no "
            "diff review, so it cannot be combined with --base, "
            "--uncommitted, --pr or --post.",
        )

    require_ai()
    resolved_ai = _resolve_ai(options=options, lintro_config=lintro_config)
    ai_config = resolved_ai.config
    if options.advisory_only:
        # Advisory-only needs no diff context and no review checklist, so it
        # deliberately bypasses the ai.review gate: the user asked for the
        # finder tools, not the diff review (#1308). --provider still applies.
        if ai_config.provider is None:
            _fail_review_command(
                AIProviderRequiredError(provider_required_error()),
                output_format=options.output_format,
                provider_label="unset",
                post=False,
                resolved_pr=None,
                effective_repo=None,
            )
        _run_advisory_only(
            advisory_tools=options.advisory_tools,
            tool_options=options.tool_options,
            path_filter=options.path_filter,
            output_format=options.output_format,
            fail_on_findings=options.fail_on_findings,
            provider_label=ai_config.provider.value,
            ai_config=ai_config,
        )

    if not ai_config.review_enabled:
        raise click.UsageError(
            "AI review is disabled. Set ai.review: true, LINTRO_AI_REVIEW=1, "
            "or pass --review (and enable ai.enabled via config or "
            "LINTRO_AI_ENABLED=1).",
        )

    targets = _resolve_targets(options=options)
    # Fail on a missing provider before git/gh work so an invalid base or a
    # non-repo cwd cannot hide the required-provider migration error.
    if ai_config.provider is None:
        _fail_review_command(
            AIProviderRequiredError(provider_required_error()),
            output_format=options.output_format,
            provider_label="unset",
            post=options.post,
            resolved_pr=targets.resolved_pr,
            effective_repo=targets.effective_repo,
        )

    prepared = _prepare(
        options=options,
        lintro_config=lintro_config,
        resolved_ai=resolved_ai,
        workspace_root=workspace_root,
        targets=targets,
    )
    _finish_review(
        options=options,
        lintro_config=lintro_config,
        resolved_ai=resolved_ai,
        prepared=prepared,
        targets=targets,
    )


def _resolve_ai(
    *,
    options: ReviewCommandOptions,
    lintro_config: LintroConfig,
) -> ResolvedAIConfig:
    """Resolve the effective AI configuration for this invocation.

    Args:
        options: The command's Click-populated options.
        lintro_config: Loaded project configuration.

    Returns:
        ResolvedAIConfig: Effective values with per-field provenance.

    Raises:
        click.UsageError: When a flag or environment override is invalid.
    """
    try:
        return resolve_effective_ai_config(
            lintro_config.ai,
            cli_overrides=AICliOverrides(
                provider=options.provider_override,
                model=options.model_override,
                transport=options.transport,
                review=options.review_override,
                max_cost_usd=options.max_cost_usd_override,
            ),
        )
    except AIConfigOverrideError as exc:
        raise click.UsageError(str(exc)) from exc


def _resolve_targets(*, options: ReviewCommandOptions) -> _ReviewTargets:
    """Validate the GitHub target flags and resolve the PR to act on.

    Args:
        options: The command's Click-populated options.

    Returns:
        _ReviewTargets: The repository and pull request this run targets.

    Raises:
        click.UsageError: When the flag combination cannot name a target.
    """
    effective_repo = options.repo or os.environ.get("GITHUB_REPOSITORY")
    if options.pr is not None and not effective_repo:
        raise click.UsageError(
            "--pr requires --repo or GITHUB_REPOSITORY environment variable.",
        )
    if options.pr is not None and options.uncommitted:
        raise click.UsageError(
            "--pr and --uncommitted cannot be used together.",
        )
    if options.pr is None and options.repo is not None and not options.post:
        raise click.UsageError("--repo can only be used with --pr.")
    resolved_pr: int | None = None
    if options.post:
        resolved_pr = options.pr or _detect_pr_number_from_env()
        if resolved_pr is None:
            raise click.UsageError(
                "--post requires --pr or a CI pull-request environment.",
            )
        if not effective_repo:
            raise click.UsageError(
                "--post requires --repo or GITHUB_REPOSITORY environment variable.",
            )
    return _ReviewTargets(
        effective_repo=effective_repo,
        resolved_pr=resolved_pr,
        # The PR detected from CI for --post is the one whose state was
        # persisted; a bare --pr without --post still names the PR directly.
        state_pr=resolved_pr if resolved_pr is not None else options.pr,
    )


def _prepare(
    *,
    options: ReviewCommandOptions,
    lintro_config: LintroConfig,
    resolved_ai: ResolvedAIConfig,
    workspace_root: Path,
    targets: _ReviewTargets,
) -> PreparedReview:
    """Build the shared review request and prepare it.

    Args:
        options: The command's Click-populated options.
        lintro_config: Loaded project configuration.
        resolved_ai: Effective AI configuration for this invocation.
        workspace_root: Absolute workspace root.
        targets: Resolved GitHub target for this run.

    Returns:
        PreparedReview: The prepared review both surfaces share.

    Raises:
        click.ClickException: When the diff context cannot be collected.
        click.UsageError: When the request describes a review that would
            review nothing.
    """
    context_pr = targets.resolved_pr if options.post else options.pr
    request = ReviewRunRequest(
        workspace_root=workspace_root,
        lintro_config=lintro_config,
        base=options.base,
        uncommitted=options.uncommitted,
        pr_number=context_pr,
        repo=targets.effective_repo if context_pr is not None else None,
        paths=tuple(options.path_filter),
        depth=options.depth,
        strictness=options.strictness,
        with_lint=options.with_lint,
        semantic_chunks=options.semantic_chunks,
        timeout=options.timeout,
        custom_agent_mode=lintro_config.review.custom_agents,
    )
    try:
        prepared = prepare_review(request, resolved=resolved_ai)
    except ReviewContextError as exc:
        raise click.ClickException(str(exc)) from exc
    except ReviewPreparationError as exc:
        raise click.UsageError(str(exc)) from exc
    if prepared.lint_digest and options.output_format == "terminal":
        logger.info(
            "Ran lint on changed files: {} tools, {} issues",
            prepared.lint_tool_count,
            prepared.lint_issue_count,
        )
    return prepared


def _finish_review(
    *,
    options: ReviewCommandOptions,
    lintro_config: LintroConfig,
    resolved_ai: ResolvedAIConfig,
    prepared: PreparedReview,
    targets: _ReviewTargets,
) -> NoReturn:
    """Run the prepared review and own everything after it.

    Declared ``NoReturn``: every path ends in the ``SystemExit`` one of the
    helpers below raises — the convergence skip, a review failure, or the
    render/post tail carrying the run's exit code.

    Args:
        options: The command's Click-populated options.
        lintro_config: Loaded project configuration.
        resolved_ai: Effective AI configuration for this invocation.
        prepared: The prepared review.
        targets: Resolved GitHub target for this run.
    """
    resolved_profile = resolve_transport_settings(prepared.ai_config)
    logger.info(
        "AI review transport profile: {}",
        format_resolved_profile_log(resolved_profile),
    )
    console = Console()
    progress_tracker = None
    if options.output_format == "terminal":
        from lintro.ai.review.progress import RichReviewProgress

        progress_tracker = RichReviewProgress(console=console)

    prior_state = _load_prior_review_state(
        pr_number=targets.state_pr,
        head_ref=prepared.context.head_ref,
        repo=targets.effective_repo or os.environ.get("GITHUB_REPOSITORY", ""),
        post=options.post,
    )
    if not options.force_full:
        _check_convergence(
            options=options,
            lintro_config=lintro_config,
            prior_state=prior_state,
            targets=targets,
        )

    cap, cap_source = resolve_max_cost_with_source(resolved_ai)
    result = _run_round(
        options=options,
        prepared=prepared,
        policy=ReviewExecutionPolicy(
            progress=progress_tracker,
            context_window_override=options.context_window,
            prior_state=prior_state,
            force_full=options.force_full,
            enforce_cost_cap=cap_is_enforced(
                source=cap_source,
                basis=resolved_profile.cost_basis,
            ),
        ),
        stamp=_MetadataStamp(
            profile=resolved_profile,
            resolved_ai=resolved_ai,
            cap=cap,
            cap_source=cap_source,
        ),
        targets=targets,
        console=console,
    )
    _render_post_and_exit(
        options=options,
        lintro_config=lintro_config,
        prepared=prepared,
        result=result,
        prior_state=prior_state,
        targets=targets,
        resolved_profile=resolved_profile,
    )


def _check_convergence(
    *,
    options: ReviewCommandOptions,
    lintro_config: LintroConfig,
    prior_state: ReviewState,
    targets: _ReviewTargets,
) -> None:
    """Skip the round when the convergence stop rule has fired.

    Evaluated before the provider is constructed, so a converged round costs
    nothing at all. ``--full`` is the always-available escape hatch that forces
    a round from CI or a manual dispatch.

    The resume ledger (#2154) is consulted alongside the run window: a flagged
    file or an unserved group/import invalidation is work the next round owes,
    and ``resume.py`` would queue it on a real round. Skipping would drop it
    silently rather than deferring it, and the score cannot see it — a round
    can finish complete and quiet while still queueing a flag for the round
    after.

    Returns normally when the round must run. When it converged,
    :func:`_finish_converged_review` stamps the outcome and raises
    ``SystemExit``, so this never returns on that path.

    Args:
        options: The command's Click-populated options.
        lintro_config: Loaded project configuration.
        prior_state: State loaded for this invocation.
        targets: Resolved GitHub target for this run.
    """
    convergence = lintro_config.review.convergence
    decision = evaluate_convergence(
        runs=prior_state.runs,
        threshold=convergence.threshold,
        stable_rounds=convergence.stable_rounds,
        pending_resume_work=bool(
            prior_state.flagged_files or prior_state.pending_invalidations,
        ),
    )
    if decision.converged:
        _finish_converged_review(
            decision=decision,
            output_format=options.output_format,
            post=options.post,
            resolved_pr=targets.resolved_pr,
            effective_repo=targets.effective_repo,
            prior_state=prior_state,
        )


def _run_round(
    *,
    options: ReviewCommandOptions,
    prepared: PreparedReview,
    policy: ReviewExecutionPolicy,
    stamp: _MetadataStamp,
    targets: _ReviewTargets,
    console: Console,
) -> ReviewResult:
    """Construct the provider, execute the review, and persist its state.

    Args:
        options: The command's Click-populated options.
        prepared: The prepared review.
        policy: CLI-owned execution knobs for the orchestrator.
        stamp: Provenance the completed run's metadata is stamped with.
        targets: Resolved GitHub target for this run.
        console: Terminal console for error rendering.

    A provider or review failure never returns: :func:`_fail_review_command`
    renders it on the surface the run asked for and raises ``SystemExit`` with
    the review-error exit code.

    Returns:
        ReviewResult: The completed review.
    """
    provider = None
    try:
        provider = get_provider(
            prepared.ai_config,
            workspace_root=prepared.workspace_root,
            transcript_command="review",
        )
        result = _stamp_metadata(
            result=execute_review(prepared, provider=provider, policy=policy),
            stamp=stamp,
        )
        if options.post and policy.prior_state is not None and not options.force_full:
            result = _replay_unposted_findings(
                result=result,
                prior_state=policy.prior_state,
                context=prepared.context,
            )
        try:
            _persist_review_state(
                result=result,
                context=prepared.context,
                prior=policy.prior_state,
                force_full=options.force_full,
                pr_number=targets.state_pr,
                repo=targets.effective_repo or os.environ.get("GITHUB_REPOSITORY", ""),
            )
        except Exception:
            logger.warning(
                "Could not persist review-resume state; next round re-reviews",
            )
    except (AIError, ValueError) as exc:
        _fail_review_command(
            exc,
            output_format=options.output_format,
            provider_label=(str(provider.name) if provider is not None else "unset"),
            post=options.post,
            resolved_pr=targets.resolved_pr,
            effective_repo=targets.effective_repo,
            console=console,
            prior_state=policy.prior_state,
        )
    return result


def _stamp_metadata(*, result: ReviewResult, stamp: _MetadataStamp) -> ReviewResult:
    """Stamp transport, provenance, and cost provenance onto the run metadata.

    The profile resolves BILLED for the api transport *before* the run; when
    the provider returned no usage counters the orchestrator set
    ``token_usage_estimated``, so the honest post-run basis is ESTIMATED.
    Stamping billed here would also suppress the legacy derivation in
    ``github_sticky._run_record``, which only fires on an empty basis.

    Args:
        result: The completed review.
        stamp: Provenance resolved for this invocation.

    Returns:
        ReviewResult: The result with provenance-complete metadata.
    """
    from lintro.ai.enums.cost_basis import CostBasis

    effective_basis = stamp.profile.cost_basis
    if result.metadata.token_usage_estimated and effective_basis is CostBasis.BILLED:
        effective_basis = CostBasis.ESTIMATED
    return replace(
        result,
        metadata=replace(
            result.metadata,
            transport=stamp.profile.transport.value,
            auth_mode=stamp.profile.auth_mode,
            cost_basis=effective_basis.value,
            provider_source=stamp.resolved_ai.source_of("provider").value,
            model_source=stamp.resolved_ai.source_of("model").value,
            transport_source=stamp.resolved_ai.source_of("transport").value,
            max_cost_usd=stamp.cap,
            max_cost_usd_source=stamp.cap_source.value,
        ),
    )


def _replay_unposted_findings(
    *,
    result: ReviewResult,
    prior_state: ReviewState,
    context: ReviewContext,
) -> ReviewResult:
    """Carry findings a previous round never posted into this round's result.

    This run's findings were guarded by finalize. Replayed findings usually
    were too and carry their tag, which the guard honours, but a SIGTERM
    checkpoint persists raw chunk findings before finalize runs, so a resumed
    round can replay an unguarded phantom P1. Guarding only the replayed rows
    closes that path without touching this run's findings (#2268 review).

    Args:
        result: The completed review.
        prior_state: State from the previous round.
        context: The review's diff context.

    Returns:
        ReviewResult: The result, extended with any replayed findings.
    """
    from lintro.ai.review.finding_matcher import review_findings_from_unposted

    replayed = review_findings_from_unposted(
        prior=prior_state,
        current=result.findings,
        reviewed_paths=frozenset(result.metadata.reviewed_paths),
    )
    if not replayed:
        return result
    return replace(
        result,
        findings=(
            *result.findings,
            *apply_cross_chunk_guard(
                findings=replayed,
                changed_paths=guard_changed_paths(context=context),
            ),
        ),
    )


def _render_post_and_exit(
    *,
    options: ReviewCommandOptions,
    lintro_config: LintroConfig,
    prepared: PreparedReview,
    result: ReviewResult,
    prior_state: ReviewState,
    targets: _ReviewTargets,
    resolved_profile: ResolvedTransportSettings,
) -> NoReturn:
    """Validate, render, optionally post, and exit with the review's code.

    Patch validation sits between parse and post (#2101): every suggestion is
    checked against the real file at head before any surface renders it, so
    ``--post`` can never publish a block that would corrupt the file when
    committed. Findings are never removed, only stripped and tagged.

    Args:
        options: The command's Click-populated options.
        lintro_config: Loaded project configuration.
        prepared: The prepared review.
        result: The completed review.
        prior_state: State loaded for this invocation.
        targets: Resolved GitHub target for this run.
        resolved_profile: The transport profile the run used.

    Raises:
        SystemExit: Always; ``1`` for a blocking outcome, ``0`` otherwise.
    """
    result = validate_result_suggested_patches(result=result, context=prepared.context)
    question_map = build_prompt_question_map(items=prepared.checklist_items)
    result = enrich_review_result(result=result, question_map=question_map)
    render = _ReviewRender(
        checklist_display=resolve_checklist_display(
            cli_value=options.show_checklist,
            config_value=lintro_config.review.checklist_display,
        ),
        question_map=question_map,
    )

    skip_post_tail = _skip_sigterm_post_tail(result=result)
    if skip_post_tail:
        logger.warning(
            "Skipping advisory tools and --post after SIGTERM so the "
            "wrapper can classify the envelope before the runner SIGKILL",
        )
        advisory_results: list[ToolResult] = []
    else:
        advisory_results = _execute_advisory(
            advisory_tools=options.advisory_tools,
            tool_options=options.tool_options,
            paths=_existing_changed_files(
                changed_files=prepared.context.changed_files,
                workspace_root=prepared.workspace_root,
            ),
            ai_config=prepared.ai_config,
        )

    _emit_output(
        options=options,
        result=result,
        render=render,
        advisory_results=advisory_results,
    )
    if options.post and not skip_post_tail:
        _post_review(
            options=options,
            lintro_config=lintro_config,
            prepared=prepared,
            result=result,
            prior_state=prior_state,
            targets=targets,
            resolved_profile=resolved_profile,
            render=render,
        )

    exit_code = 1 if result.has_p1_findings else 0
    if options.fail_on_findings and advisory_findings_count(advisory_results):
        exit_code = 1
    raise SystemExit(exit_code)


def _emit_output(
    *,
    options: ReviewCommandOptions,
    result: ReviewResult,
    render: _ReviewRender,
    advisory_results: list[ToolResult],
) -> None:
    """Render the review and advisory results to stdout.

    Args:
        options: The command's Click-populated options.
        result: The completed review.
        render: Checklist rendering and attribution for this run.
        advisory_results: Advisory tool results for this run.
    """
    output = render_review_output(
        result=result,
        output_format=options.output_format,
        checklist_display=render.checklist_display,
        question_map=render.question_map,
    )
    if options.output_format == "json":
        output = _merge_advisory_into_json(
            review_output=output,
            advisory_results=advisory_results,
        )
    if output is not None:
        click.echo(output)
    if options.output_format != "json":
        advisory_text = render_advisory_results(results=advisory_results)
        if advisory_text:
            click.echo(f"\n{advisory_text}")


def _post_review(
    *,
    options: ReviewCommandOptions,
    lintro_config: LintroConfig,
    prepared: PreparedReview,
    result: ReviewResult,
    prior_state: ReviewState,
    targets: _ReviewTargets,
    resolved_profile: ResolvedTransportSettings,
    render: _ReviewRender,
) -> None:
    """Post the review to GitHub and persist the comment ids it captured.

    Args:
        options: The command's Click-populated options.
        lintro_config: Loaded project configuration.
        prepared: The prepared review.
        result: The completed review.
        prior_state: State loaded for this invocation.
        targets: Resolved GitHub target for this run.
        resolved_profile: The transport profile the run used.
        render: Checklist rendering and attribution for this run.
    """
    from lintro.ai.review.github import post_review_to_github

    captured_comment_ids: dict[str, int] = {}
    posted = post_review_to_github(
        result=result,
        pr_number=targets.resolved_pr,
        repo=targets.effective_repo,
        prior_state=prior_state,
        departed_paths=_departed_paths(context=prepared.context),
        checklist_display=render.checklist_display,
        question_map=render.question_map,
        transport=resolved_profile.transport.value,
        auth_mode=resolved_profile.auth_mode,
        # metadata carries the post-run reconciled basis (estimated when the
        # provider reported no usage), not the pre-run profile value.
        cost_basis=result.metadata.cost_basis,
        auto_resolve=lintro_config.review.auto_resolve,
        config_source=_describe_config_source(
            config_path=lintro_config.config_path,
            overrides=_cli_overrides(options=options),
        ),
        captured_comment_ids=captured_comment_ids,
    )
    if captured_comment_ids:
        try:
            _persist_review_state(
                result=result,
                context=prepared.context,
                prior=prior_state,
                force_full=options.force_full,
                pr_number=targets.state_pr,
                repo=targets.effective_repo or os.environ.get("GITHUB_REPOSITORY", ""),
                inline_comment_ids=captured_comment_ids,
            )
        except Exception:
            logger.warning(
                "Could not persist posted inline comment ids; next "
                "round may replay those findings",
            )
    if not posted:
        logger.warning("GitHub review posting skipped or failed")


def _cli_overrides(*, options: ReviewCommandOptions) -> list[str]:
    """List the CLI flags that overrode configured review settings.

    Only explicitly-passed options are listed: the point of the note is to
    explain why a posted run's stats differ from the checked-in config, so
    defaults would be noise.

    Args:
        options: The command's Click-populated options.

    Returns:
        Rendered flag strings in CLI order.
    """
    overrides: list[str] = []
    if options.depth is not None:
        overrides.append(f"--depth {options.depth}")
    if options.strictness is not None:
        overrides.append(f"--strictness {options.strictness}")
    if options.transport is not None:
        overrides.append(f"--transport {options.transport}")
    if options.provider_override is not None:
        overrides.append(f"--provider {options.provider_override}")
    if options.model_override is not None:
        overrides.append(f"--model {options.model_override}")
    if options.review_override is not None:
        overrides.append("--review" if options.review_override else "--no-review")
    if options.max_cost_usd_override is not None:
        overrides.append(f"--max-cost-usd {options.max_cost_usd_override}")
    if options.timeout is not None:
        overrides.append(f"--timeout {options.timeout:g}")
    if options.context_window is not None:
        overrides.append(f"--context-window {options.context_window}")
    if options.semantic_chunks:
        overrides.append("--semantic-chunks")
    overrides.extend(f"--path {path}" for path in options.path_filter)
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


def _skip_sigterm_post_tail(*, result: ReviewResult) -> bool:
    """Return True when runner SIGTERM left only the persist window.

    GitHub Actions SIGKILLs the review step ~5–7s after SIGTERM. Coverage
    is already on disk (incremental parts plus the final persist above).
    Advisory tools and ``--post`` can burn that window so the wrapper
    never classifies the JSON envelope and ``if: always()`` upload is
    skipped. The next resume run posts the sticky and inlines.

    Args:
        result: Completed or partial review result.

    Returns:
        True when this run is a SIGTERM partial and must exit immediately
        after writing the envelope.
    """
    return result.metadata.partial and "SIGTERM" in result.metadata.stopped_reason


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

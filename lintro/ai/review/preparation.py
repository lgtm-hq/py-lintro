"""Shared review preparation and execution for every review adapter (#2300).

``lintro review`` and the MCP ``lintro_review`` tool used to perform the same
sequence independently: collect the diff context, classify the changed files,
select and format the checklist, build the optional lint digest, resolve
sensitivity and custom agents, and finally call
:func:`~lintro.ai.review.orchestrator.run_review`. Two copies of one sequence
drift, which is exactly what epic #1972 (problem 2) records.

This module is that sequence, once:

* :class:`ReviewRunRequest` — the typed inputs a review needs, built by an
  adapter from its own argument surface (Click options, an MCP envelope).
* :func:`prepare_review` — deterministic, provider-free preparation. It reads
  git and the workspace config and returns a :class:`PreparedReview`; it never
  constructs a provider and never issues a provider call, so two adapters
  handed equal requests produce equal prepared reviews.
* :func:`execute_review` — the one call into the orchestrator, with the
  adapter's own execution policy (:class:`ReviewExecutionPolicy`) passed
  explicitly rather than hidden behind callbacks.

What stays with the adapters is policy, not preparation: Click validation and
usage errors, MCP's envelope and workspace session, terminal/JSON rendering,
GitHub posting, resume-state persistence, the MCP budget clamp, and process
exit codes. See ADR-0006 section B for the ownership table.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from loguru import logger

from lintro.ai.review import (
    classify_changed_files,
    collect_review_context,
    format_checklist_for_prompt,
    get_all_checklist_items,
    select_checklist_items,
)
from lintro.ai.review.custom_agents import discover_custom_agents
from lintro.ai.review.enums.custom_agent_mode import CustomAgentMode
from lintro.ai.review.enums.review_strictness import ReviewStrictness
from lintro.ai.review.exceptions import ReviewPreparationError
from lintro.ai.review.lint_bridge import (
    format_lint_results_for_prompt,
    run_lint_on_changed_files,
)
from lintro.ai.review.orchestrator import run_review
from lintro.ai.review.sensitivity import resolve_sensitivity_policy
from lintro.ai.review.session import ReviewSessionOptions
from lintro.ai.transport import apply_resolved_transport

if TYPE_CHECKING:
    from pathlib import Path

    from lintro.ai.config import AIConfig
    from lintro.ai.providers.base import BaseAIProvider
    from lintro.ai.resolved_ai_config import ResolvedAIConfig
    from lintro.ai.review.custom_agents import CustomAgentSpec
    from lintro.ai.review.models import (
        ChecklistItem,
        FileClassification,
        ReviewContext,
    )
    from lintro.ai.review.models.review_result import ReviewResult
    from lintro.ai.review.models.review_state import ReviewState
    from lintro.ai.review.progress import ReviewProgressCallback
    from lintro.ai.review.sensitivity import ReviewSensitivityPolicy
    from lintro.config.lintro_config import LintroConfig
    from lintro.config.review_config import ReviewSynthesisConfig

__all__ = [
    "DEFAULT_EXECUTION_POLICY",
    "PreparedReview",
    "ReviewExecutionPolicy",
    "ReviewRunRequest",
    "execute_review",
    "prepare_review",
    "resolve_custom_agent_mode",
    "resolve_review_depth",
    "resolve_review_strictness",
]


@dataclass(frozen=True, slots=True)
class ReviewRunRequest:
    """One adapter's request for a review, before anything has been resolved.

    Every field is an input an adapter genuinely owns: the diff selection, the
    review shape, and the workspace the review is anchored to. The ``None``
    fields mean "not requested", and :func:`prepare_review` falls back to the
    project config for those — so a request built from an MCP envelope and one
    built from Click options resolve identically.

    Attributes:
        workspace_root: Absolute workspace root the review is anchored to.
        lintro_config: Loaded project configuration. The ``review:`` section
            supplies every default the request leaves unset.
        base: Base git ref for the diff, or None for the default branch.
        uncommitted: Review staged and unstaged working-tree changes.
        pr_number: GitHub pull request number to review, when the diff comes
            from ``gh`` rather than a local branch.
        repo: ``owner/name`` repository for the pull-request diff.
        paths: Path prefixes the review is limited to; empty means the whole
            diff.
        depth: Requested review depth (1-3), or None for ``review.depth``.
        strictness: Requested strictness preset, or None for
            ``review.strictness``.
        with_lint: Run lintro on the changed files and include a digest of
            the results in the review prompt.
        semantic_chunks: Force semantic chunking for this run. Config's
            ``review.force_semantic_chunking`` can enable it independently.
        timeout: Per-run API timeout override in seconds, or None.
        custom_agent_mode: How user-defined review agents participate, or
            None for ``review.custom_agents``. A caller that deliberately
            wants the built-in checklist only passes
            :attr:`CustomAgentMode.DISABLED` explicitly.
    """

    workspace_root: Path
    lintro_config: LintroConfig
    base: str | None = None
    uncommitted: bool = False
    pr_number: int | None = None
    repo: str | None = None
    paths: tuple[str, ...] = ()
    depth: int | None = None
    strictness: str | None = None
    with_lint: bool = False
    semantic_chunks: bool = False
    timeout: float | None = None
    custom_agent_mode: CustomAgentMode | None = None


@dataclass(frozen=True, slots=True)
class PreparedReview:
    """Everything a review run needs, resolved once and shared by both surfaces.

    Two adapters that build equal :class:`ReviewRunRequest` values over the
    same workspace must produce equal ``PreparedReview`` values — that equality
    is the parity contract ``tests/unit/ai/review/test_cli_mcp_parity.py``
    asserts. ``context_collection_seconds`` is wall-clock and is therefore
    excluded from equality while still travelling with the value.

    Attributes:
        ai_config: Effective AI configuration with the request's timeout and
            the resolved transport profile already applied.
        context: Collected review diff context.
        classifications: Domain classification per changed file.
        checklist_items: Checklist items selected for this diff.
        checklist_text: Checklist rendered for the prompt.
        depth: Effective review depth.
        strictness: Effective strictness preset.
        sensitivity: Sensitivity policy resolved from the preset and config
            overrides.
        force_semantic_chunking: Whether the single-chunk fast path is skipped.
        custom_agents: Discovered user-defined review agents.
        run_builtin_checklist: Whether the built-in checklist passes run.
        synthesis: Cross-chunk synthesis configuration (#2269).
        workspace_root: Absolute workspace root the review is anchored to.
        lint_digest: ``--with-lint`` digest for the prompt, or None.
        lint_tool_count: Number of lint tools that ran for the digest.
        lint_issue_count: Total issues those tools reported.
        context_collection_seconds: Wall-clock seconds spent collecting the
            diff context. Excluded from equality: it measures the run, not the
            preparation.
    """

    ai_config: AIConfig
    context: ReviewContext
    classifications: list[FileClassification]
    checklist_items: list[ChecklistItem]
    checklist_text: str
    depth: int
    strictness: ReviewStrictness
    sensitivity: ReviewSensitivityPolicy
    force_semantic_chunking: bool
    custom_agents: tuple[CustomAgentSpec, ...]
    run_builtin_checklist: bool
    synthesis: ReviewSynthesisConfig
    workspace_root: Path
    lint_digest: str | None = None
    lint_tool_count: int = 0
    lint_issue_count: int = 0
    context_collection_seconds: float = field(default=0.0, compare=False)

    def with_max_cost_usd(self, *, max_cost_usd: float | None) -> PreparedReview:
        """Return a copy whose effective spend ceiling is ``max_cost_usd``.

        The clamp itself is adapter policy — MCP's per-call ``max_cost_usd``
        argument may only lower the operator's ceiling (ADR-0008 invariant 6) —
        but applying it must not mean rebuilding the prepared review, so the
        shared layer owns the copy.

        Args:
            max_cost_usd: The ceiling to run under, or None for uncapped.

        Returns:
            PreparedReview: A copy carrying the new ceiling.
        """
        return replace(
            self,
            ai_config=self.ai_config.model_copy(
                update={"max_cost_usd": max_cost_usd},
            ),
        )


@dataclass(frozen=True, slots=True)
class ReviewExecutionPolicy:
    """Adapter-owned knobs that are not part of deterministic preparation.

    Each field is something one surface has and the other genuinely does not:
    terminal progress, the CLI's ``--context-window`` flag, and the CLI's
    resume state and cost-cap gate. They are passed explicitly so a reader can
    see exactly where the two surfaces differ; MCP uses
    :data:`DEFAULT_EXECUTION_POLICY` unchanged.

    Attributes:
        progress: Live progress callback for terminal output.
        context_window_override: Explicit context-window size in tokens.
        prior_state: Resume state from a previous round.
        force_full: Discard carried coverage and review everything again.
        enforce_cost_cap: Honor ``ai.max_cost_usd`` and serialize chunk calls.
    """

    progress: ReviewProgressCallback | None = None
    context_window_override: int | None = None
    prior_state: ReviewState | None = None
    force_full: bool = False
    enforce_cost_cap: bool = True


#: The policy of a surface with no adapter-specific execution knobs. Its values
#: are exactly ``run_review``'s own defaults, so passing it changes nothing.
DEFAULT_EXECUTION_POLICY = ReviewExecutionPolicy()


def resolve_review_depth(request: ReviewRunRequest) -> int:
    """Resolve the depth a request runs at.

    Split out of :func:`prepare_review` so an adapter answering "nothing
    changed" reports the depth the review *would* have used without repeating
    the fallback rule.

    Args:
        request: The adapter's typed request.

    Returns:
        int: The requested depth, or ``review.depth`` when unset.
    """
    if request.depth is not None:
        return request.depth
    return request.lintro_config.review.depth


def resolve_review_strictness(request: ReviewRunRequest) -> ReviewStrictness:
    """Resolve the strictness preset a request runs at.

    Args:
        request: The adapter's typed request.

    Returns:
        ReviewStrictness: The requested preset, or ``review.strictness`` when
        unset.
    """
    configured = request.lintro_config.review.strictness.value
    return ReviewStrictness((request.strictness or configured).lower())


def resolve_custom_agent_mode(request: ReviewRunRequest) -> CustomAgentMode:
    """Resolve how user-defined review agents participate in a request.

    Follows the same None-means-config rule as :func:`resolve_review_depth`
    and :func:`resolve_review_strictness`, so a caller that omits the field
    gets the workspace's configured mode rather than silently running the
    built-in checklist only.

    Args:
        request: The adapter's typed request.

    Returns:
        CustomAgentMode: The requested mode, or ``review.custom_agents`` when
        unset.
    """
    if request.custom_agent_mode is not None:
        return request.custom_agent_mode
    return request.lintro_config.review.custom_agents


def _apply_timeout(config: AIConfig, *, timeout: float | None) -> AIConfig:
    """Apply an explicit per-run timeout to the effective AI configuration.

    An explicit timeout wins over the transport profile for this run, so it is
    written into the active transport's own timeout as well as ``api_timeout``.
    When no transport is resolved yet, only ``api_timeout`` is written — the
    behaviour ``review_command`` had before #2300 extracted this, preserved
    deliberately rather than fixed in a refactor.

    Args:
        config: Effective AI configuration before the transport profile is
            applied.
        timeout: Requested timeout in seconds, or None to leave the
            configuration untouched.

    Returns:
        AIConfig: The configuration to resolve the transport profile from.
    """
    if timeout is None:
        return config
    updated = config.model_copy(update={"api_timeout": timeout})
    if updated.transport is None:
        return updated
    transports = updated.transports.model_copy(deep=True)
    if updated.transport.value == "cli":
        transports.cli.timeout = timeout
    else:
        transports.api.timeout = timeout
    return updated.model_copy(update={"transports": transports})


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
        tuple[CustomAgentSpec, ...]: The discovered agents, or an empty tuple
        when discovery is disabled.

    Raises:
        ReviewPreparationError: When ``mode`` is ``only`` and no valid agents
            were discovered, since the built-in checklist is skipped in that
            mode and running would silently review nothing.
    """
    if mode == CustomAgentMode.DISABLED:
        return ()
    discovery = discover_custom_agents(workspace_root=workspace_root)
    for issue in discovery.issues:
        logger.warning("Skipping invalid review agent — {issue}", issue=issue.format())
    if mode == CustomAgentMode.ONLY and not discovery.agents:
        msg = (
            "review.custom_agents is 'only' but no valid agents were found "
            f"in {discovery.directory}; the built-in checklist is skipped in "
            "'only' mode, so there is nothing left to review. Add a valid "
            "agent file or change review.custom_agents."
        )
        raise ReviewPreparationError(msg)
    return discovery.agents


def _build_lint_digest(
    *,
    context: ReviewContext,
    lintro_config: LintroConfig,
) -> tuple[str | None, int, int]:
    """Run lintro over the changed files and digest the results.

    Args:
        context: Collected review context.
        lintro_config: Loaded project configuration.

    Returns:
        tuple[str | None, int, int]: The prompt digest (None when empty), the
        number of tools that ran, and the total issues they reported.
    """
    results = run_lint_on_changed_files(
        changed_files=[file.path for file in context.changed_files],
        lintro_config=lintro_config,
    )
    digest = format_lint_results_for_prompt(results=results)
    issues = sum(result.issues_count or 0 for result in results)
    return (digest or None), len(results), issues


def prepare_review(
    request: ReviewRunRequest,
    *,
    resolved: ResolvedAIConfig,
) -> PreparedReview:
    """Resolve everything a review run needs, without touching a provider.

    Deterministic given the workspace and the request: it reads git, the
    project config, and the resolved AI config, and it never constructs a
    provider or issues a provider call. Both adapters call it, which is what
    keeps their preparation from drifting (epic #1972, Phase 3).

    Args:
        request: The adapter's typed request.
        resolved: The effective AI configuration for this invocation, from
            :func:`~lintro.ai.effective_config.resolve_effective_ai_config`.

    Two failures propagate from the helpers this calls, and each adapter
    translates them into its own error surface rather than the shared layer
    picking one: ``ReviewContextError`` when the diff context cannot be
    collected, and ``ReviewPreparationError`` when the request resolves to a
    review that would review nothing.

    Returns:
        PreparedReview: The prepared review, ready for :func:`execute_review`.
    """
    review_config = request.lintro_config.review
    ai_config = apply_resolved_transport(
        _apply_timeout(resolved.config, timeout=request.timeout),
    )

    context_started = time.monotonic()
    context = collect_review_context(
        base=request.base,
        uncommitted=request.uncommitted,
        pr_number=request.pr_number,
        repo=request.repo,
        paths=list(request.paths) or None,
        exclude_globs=list(ai_config.exclude_paths),
    )
    context_collection_seconds = time.monotonic() - context_started

    classifications = classify_changed_files(context.changed_files)
    selected_items = select_checklist_items(
        classifications=classifications,
        items=get_all_checklist_items(config=request.lintro_config),
    )
    checklist_text, _prompt_mapping = format_checklist_for_prompt(items=selected_items)

    lint_digest, lint_tool_count, lint_issue_count = (
        _build_lint_digest(context=context, lintro_config=request.lintro_config)
        if request.with_lint
        else (None, 0, 0)
    )

    strictness = resolve_review_strictness(request)
    custom_agent_mode = resolve_custom_agent_mode(request)
    return PreparedReview(
        ai_config=ai_config,
        context=context,
        classifications=classifications,
        checklist_items=selected_items,
        checklist_text=checklist_text,
        depth=resolve_review_depth(request),
        strictness=strictness,
        sensitivity=resolve_sensitivity_policy(
            strictness=strictness,
            overrides=review_config.sensitivity,
        ),
        force_semantic_chunking=(
            request.semantic_chunks or review_config.force_semantic_chunking
        ),
        custom_agents=_resolve_custom_agents(
            mode=custom_agent_mode,
            workspace_root=request.workspace_root,
        ),
        run_builtin_checklist=custom_agent_mode != CustomAgentMode.ONLY,
        synthesis=review_config.synthesis,
        workspace_root=request.workspace_root,
        lint_digest=lint_digest,
        lint_tool_count=lint_tool_count,
        lint_issue_count=lint_issue_count,
        context_collection_seconds=context_collection_seconds,
    )


def execute_review(
    prepared: PreparedReview,
    *,
    provider: BaseAIProvider,
    policy: ReviewExecutionPolicy = DEFAULT_EXECUTION_POLICY,
) -> ReviewResult:
    """Run the prepared review through the orchestrator facade.

    The provider is supplied by the adapter rather than built here: provider
    lifetime stays with the surface that constructed it (ADR-0006 section D,
    pending #1972 Phase 5), and each adapter labels its own failures with it.

    Args:
        prepared: The prepared review from :func:`prepare_review`.
        provider: Configured AI provider instance.
        policy: Adapter-owned execution knobs. Defaults to
            :data:`DEFAULT_EXECUTION_POLICY`, which matches the
            :class:`~lintro.ai.review.session.ReviewSessionOptions` defaults.

    Returns:
        ReviewResult: The completed review.
    """
    return run_review(
        prepared.context,
        options=ReviewSessionOptions(
            provider=provider,
            ai_config=prepared.ai_config,
            depth=prepared.depth,
            checklist_items=prepared.checklist_items,
            checklist_text=prepared.checklist_text,
            classifications=prepared.classifications,
            context_window_override=policy.context_window_override,
            lint_results=prepared.lint_digest,
            progress=policy.progress,
            sensitivity=prepared.sensitivity,
            force_semantic_chunking=prepared.force_semantic_chunking,
            custom_agents=prepared.custom_agents,
            run_builtin_checklist=prepared.run_builtin_checklist,
            workspace_root=prepared.workspace_root,
            context_collection_seconds=prepared.context_collection_seconds,
            prior_state=policy.prior_state,
            force_full=policy.force_full,
            enforce_cost_cap=policy.enforce_cost_cap,
            synthesis=prepared.synthesis,
        ),
    )

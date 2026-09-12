"""Request resolvers and preparation helpers shared by both review adapters.

Split out of :mod:`lintro.ai.review.preparation` (#2301) so that module holds
the request/result types and the two entry points, while the per-field
resolution rules live here. Behaviour is unchanged: every function was moved
verbatim, and the three ``resolve_*`` entry points are re-exported from
:mod:`lintro.ai.review.preparation` for existing importers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from lintro.ai.review.custom_agents import discover_custom_agents
from lintro.ai.review.enums.custom_agent_mode import CustomAgentMode
from lintro.ai.review.enums.review_strictness import ReviewStrictness
from lintro.ai.review.exceptions import ReviewPreparationError
from lintro.ai.review.lint_bridge import (
    LintReportError,
    format_lint_results_for_prompt,
    load_lint_report,
    restrict_lint_results_to_files,
    run_lint_on_changed_files,
)

if TYPE_CHECKING:
    from pathlib import Path

    from lintro.ai.config import AIConfig
    from lintro.ai.review.custom_agents import CustomAgentSpec
    from lintro.ai.review.models import ReviewContext
    from lintro.ai.review.preparation import ReviewRunRequest
    from lintro.config.lintro_config import LintroConfig

__all__ = [
    "apply_timeout",
    "build_lint_digest",
    "resolve_custom_agent_mode",
    "resolve_custom_agents",
    "resolve_review_depth",
    "resolve_review_strictness",
]


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


def apply_timeout(config: AIConfig, *, timeout: float | None) -> AIConfig:
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


def resolve_custom_agents(
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


def build_lint_digest(
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


#: Header wording for a run that asked for a saved lint report and got none.
#: Fixed text so CI logs, the posted header, and the docs say the same thing.
LINT_FACTS_UNAVAILABLE: str = "linter facts unavailable for this head"


def lint_facts_missing_note(reason: str) -> str:
    """Render the header note for a review that was given no lint report.

    The CI wiring passes the reason through ``--lint-report-missing`` (#2571)
    so the posted comment says the review ran without deterministic lint
    facts, in the same words the Actions log uses.

    Args:
        reason: Why no report was available, e.g. ``no linting-json-report
            for head <sha> after 600s``.

    Returns:
        str: ``"<LINT_FACTS_UNAVAILABLE>: <reason>"``, or an empty string when
        the reason is blank so callers can assign it unconditionally.
    """
    reason = reason.strip()
    return f"{LINT_FACTS_UNAVAILABLE}: {reason}" if reason else ""


def build_lint_digest_from_report(
    *,
    context: ReviewContext,
    report_path: Path,
) -> tuple[str | None, int, int, str]:
    """Digest a saved lintro JSON report for the prompt (``--lint-report``).

    The report was produced elsewhere — in CI, by the untrusted lint job on
    the PR head (#2571) — so nothing is executed here: the file is loaded,
    validated, restricted to the review's changed files, and formatted exactly
    as a ``--with-lint`` run would be. A report that cannot be used is not an
    error: the review proceeds from the diff alone and the returned note says
    why, for the review header.

    Args:
        context: Collected review context.
        report_path: Path to the saved report.

    Returns:
        tuple[str | None, int, int, str]: The prompt digest (None when
        empty), the number of tools in the report, the issues kept after
        restricting to changed files, and the header note (empty when the
        report was usable).
    """
    try:
        results = load_lint_report(report_path)
    except LintReportError as exc:
        logger.warning("{}: {}", LINT_FACTS_UNAVAILABLE, exc)
        return None, 0, 0, f"{LINT_FACTS_UNAVAILABLE}: {exc}"
    restricted = restrict_lint_results_to_files(
        results=results,
        changed_files=[file.path for file in context.changed_files],
    )
    digest = format_lint_results_for_prompt(results=restricted)
    issues = sum(result.issues_count or 0 for result in restricted)
    return (digest or None), len(results), issues, ""

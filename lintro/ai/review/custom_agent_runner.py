"""Execution of user-defined review agent passes (issue #1245).

Each selected agent gets one provider call scoped to the changed files its
globs match. Passes run sequentially and go through the same
:class:`~lintro.ai.budget.CostBudget` as the built-in checklist passes, so a
custom agent consumes run budget exactly like a checklist chunk does.

Safety posture: the agent body is maintainer-authored workspace content, so it
is treated as untrusted data. It never becomes the system prompt — the system
prompt is lintro's own, and the body is embedded in the *user* prompt inside a
per-call unique boundary marker after passing through
:func:`~lintro.ai.sanitize.sanitize_code_content` and the shared secret
redaction choke point.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from lintro.ai.exceptions import AICostBudgetExceededError, AIError
from lintro.ai.invoke import call_ai
from lintro.ai.json_response import strip_json_fences
from lintro.ai.prompts.review import (
    REVIEW_CUSTOM_AGENT_OUTPUT_SCHEMA,
    REVIEW_CUSTOM_AGENT_SYSTEM,
    REVIEW_CUSTOM_AGENT_USER_PROMPT_TEMPLATE,
)
from lintro.ai.review.context.diff_parse import split_unified_diff_by_file
from lintro.ai.review.custom_agents import CustomAgentSpec, SelectedCustomAgent
from lintro.ai.review.finding_parser import parse_findings
from lintro.ai.review.models.review_finding import ReviewFinding
from lintro.ai.review.prompt_redaction import redact_prompt_text
from lintro.ai.review.sensitivity import (
    format_strictness_prompt_section,
    resolve_sensitivity_policy,
)
from lintro.ai.sanitize import (
    detect_injection_patterns,
    make_boundary_marker,
    sanitize_code_content,
)

if TYPE_CHECKING:
    from lintro.ai.budget import CostBudget
    from lintro.ai.config import AIConfig
    from lintro.ai.providers.base import BaseAIProvider
    from lintro.ai.review.models.review_context import ReviewContext

__all__ = [
    "CustomAgentPassRequest",
    "CustomAgentPassResult",
    "build_custom_agent_prompt",
    "run_custom_agent_passes",
    "scope_diff_to_files",
]


@dataclass(frozen=True, slots=True)
class CustomAgentPassResult:
    """Outcome of one custom review agent pass.

    Attributes:
        agent_name: Name of the agent that produced the pass.
        findings: Findings reported by the agent, already attributed via
            :attr:`~lintro.ai.review.models.review_finding.ReviewFinding.source`.
        files: Changed files this pass actually reviewed.
        input_tokens: Prompt tokens consumed by the pass.
        output_tokens: Completion tokens produced by the pass.
        cost_estimate: Estimated USD cost of the pass.
    """

    agent_name: str
    findings: tuple[ReviewFinding, ...] = ()
    files: tuple[str, ...] = ()
    input_tokens: int = 0
    output_tokens: int = 0
    cost_estimate: float = 0.0


def scope_diff_to_files(*, unified_diff: str, files: tuple[str, ...]) -> str:
    """Slice a unified diff down to the sections for specific files.

    Args:
        unified_diff: Full unified diff text for the review range.
        files: Repository-relative paths to keep.

    Returns:
        The concatenated per-file diff sections, in ``files`` order. Falls back
        to the full diff when no per-file section can be resolved (e.g. an
        unparseable diff), so an agent is never handed an empty diff.
    """
    per_file = split_unified_diff_by_file(unified_diff=unified_diff)
    sections = [per_file[path] for path in files if path in per_file]
    if not sections:
        return unified_diff
    return "".join(sections)


def build_custom_agent_prompt(
    *,
    agent: CustomAgentSpec,
    files: tuple[str, ...],
    diff: str,
) -> str:
    """Build the user prompt for one custom review agent pass.

    The agent body is sanitized and fenced by a per-call unique boundary marker
    so maintainer-authored prose cannot impersonate prompt structure or forge
    the fence that closes its own data block. The same marker fences the scoped
    diff so workspace-derived content cannot terminate surrounding tags.

    Args:
        agent: The agent whose instructions are embedded.
        files: Changed files the agent is scoped to.
        diff: Diff text scoped to those files.

    Returns:
        The rendered user prompt.
    """
    detected = detect_injection_patterns(agent.body)
    if detected:
        logger.warning(
            "Custom review agent {agent} body contains prompt-injection-like "
            "patterns ({patterns}); embedding it as inert data.",
            agent=agent.name,
            patterns=", ".join(detected),
        )
    instructions = sanitize_code_content(
        redact_prompt_text(
            text=agent.body,
            source=f"custom agent {agent.name}",
        ),
    )
    policy = resolve_sensitivity_policy(strictness=agent.strictness)
    return REVIEW_CUSTOM_AGENT_USER_PROMPT_TEMPLATE.format(
        agent_name=agent.name,
        agent_description=agent.description or "(none declared)",
        scoped_file_count=len(files),
        scoped_files="\n".join(f"- {path}" for path in files),
        boundary=make_boundary_marker(),
        agent_instructions=instructions,
        diff=redact_prompt_text(text=diff, source="diff"),
        strictness_section=format_strictness_prompt_section(policy=policy),
        output_schema=REVIEW_CUSTOM_AGENT_OUTPUT_SCHEMA,
    )


def _resolve_provider(
    *,
    agent: CustomAgentSpec,
    provider: BaseAIProvider,
    ai_config: AIConfig,
    cache: dict[str, BaseAIProvider],
    workspace_root: Path | None,
) -> tuple[BaseAIProvider, AIConfig]:
    """Resolve the provider and config for an agent's optional model override.

    Args:
        agent: The agent being executed.
        provider: The run's default provider.
        ai_config: The run's AI configuration.
        cache: Per-run cache of providers keyed by overridden model name.
        workspace_root: Optional workspace root for provider construction.

    Returns:
        Tuple of provider and config to use for this agent's call. Falls back
        to the run defaults when the override cannot be constructed.
    """
    if not agent.model or agent.model == ai_config.model:
        return provider, ai_config

    agent_config = ai_config.model_copy(update={"model": agent.model})
    cached = cache.get(agent.model)
    if cached is not None:
        return cached, agent_config

    from lintro.ai.providers import get_provider

    try:
        agent_provider = get_provider(agent_config, workspace_root=workspace_root)
    except (AIError, ValueError) as error:
        logger.warning(
            "Custom review agent {agent} requested model {model} but the "
            "provider could not be built ({error}); using the configured model.",
            agent=agent.name,
            model=agent.model,
            error=error,
        )
        return provider, ai_config

    cache[agent.model] = agent_provider
    return agent_provider, agent_config


def _findings_from_response(
    *,
    agent: CustomAgentSpec,
    content: str,
) -> tuple[ReviewFinding, ...]:
    """Parse an agent response payload into attributed findings.

    Args:
        agent: The agent that produced the response.
        content: Raw model response text.

    Returns:
        Parsed findings, empty when the payload is unusable.
    """
    try:
        payload = json.loads(strip_json_fences(content=content))
    except (json.JSONDecodeError, ValueError):
        logger.warning(
            "Custom review agent {agent} returned unparseable JSON; skipping "
            "its findings.",
            agent=agent.name,
        )
        return ()
    if not isinstance(payload, dict):
        logger.warning(
            "Custom review agent {agent} response was not a JSON object.",
            agent=agent.name,
        )
        return ()
    return parse_findings(
        raw_findings=payload.get("findings", []),
        source=agent.name,
        severity_override=agent.severity,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class CustomAgentPassRequest:
    """Everything one run's custom-agent passes need.

    Grouping the run-scope inputs keeps :func:`run_custom_agent_passes` to one
    argument and makes a new input a field here rather than another keyword
    threaded through the caller (issue #2301).

    Attributes:
        selected: Agents scoped to at least one changed file.
        context: Collected review diff context.
        provider: The run's default provider.
        ai_config: The run's AI configuration.
        budget: Session cost budget shared with the built-in passes.
        repo_root: Absolute path to the repository under review.
        workspace_root: Optional workspace root for per-agent providers.
        use_one_shot: When True, avoid durable CLI provider sessions.
        on_pass_complete: Optional callback invoked with each completed pass
            as soon as it finishes, so a caller can recover work already done
            if a later pass trips the cost cap.
        on_agent_failed: Optional callback invoked with the agent's name when
            a non-budget provider error skips it, so a caller can count it
            toward skipped-agent metadata (issue #1245).
    """

    selected: tuple[SelectedCustomAgent, ...]
    context: ReviewContext
    provider: BaseAIProvider
    ai_config: AIConfig
    budget: CostBudget
    repo_root: str = ""
    workspace_root: Path | None = None
    use_one_shot: bool = True
    on_pass_complete: Callable[[CustomAgentPassResult], None] | None = None
    on_agent_failed: Callable[[str], None] | None = None


async def run_custom_agent_passes(
    *,
    request: CustomAgentPassRequest,
) -> tuple[CustomAgentPassResult, ...]:
    """Run every selected custom review agent against its scoped diff.

    Passes run sequentially. A cost-cap stop propagates so the caller can
    finalize a partial review; any other provider failure is logged and that
    single agent is skipped, because one bad workspace-authored agent must not
    discard an otherwise complete review.

    Args:
        request: The run-scope inputs every pass reads.

    Returns:
        Results for every agent that completed a pass.

    Raises:
        AICostBudgetExceededError: When the session cost cap is reached.
    """
    selected = request.selected
    context = request.context
    provider = request.provider
    ai_config = request.ai_config
    budget = request.budget
    repo_root = request.repo_root
    workspace_root = request.workspace_root
    use_one_shot = request.use_one_shot
    on_pass_complete = request.on_pass_complete
    on_agent_failed = request.on_agent_failed

    results: list[CustomAgentPassResult] = []
    provider_cache: dict[str, BaseAIProvider] = {}
    for entry in selected:
        agent = entry.agent
        budget.check()
        agent_provider, agent_config = _resolve_provider(
            agent=agent,
            provider=provider,
            ai_config=ai_config,
            cache=provider_cache,
            workspace_root=workspace_root,
        )
        prompt = build_custom_agent_prompt(
            agent=agent,
            files=entry.files,
            diff=scope_diff_to_files(
                unified_diff=context.unified_diff,
                files=entry.files,
            ),
        )
        logger.info(
            "Running custom review agent {agent} on {count} file(s)",
            agent=agent.name,
            count=len(entry.files),
        )
        try:
            response = await call_ai(
                provider=agent_provider,
                ai_config=agent_config,
                system_prompt=REVIEW_CUSTOM_AGENT_SYSTEM,
                user_prompt=prompt,
                budget=budget,
                repo_root=repo_root or None,
                use_one_shot=use_one_shot,
            )
        except AICostBudgetExceededError:
            raise
        except AIError as error:
            logger.error(
                "Custom review agent {agent} failed: {error}",
                agent=agent.name,
                error=error,
            )
            if on_agent_failed is not None:
                on_agent_failed(agent.name)
            continue

        result = CustomAgentPassResult(
            agent_name=agent.name,
            findings=_findings_from_response(
                agent=agent,
                content=response.content,
            ),
            files=entry.files,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cost_estimate=response.cost_estimate,
        )
        results.append(result)
        if on_pass_complete is not None:
            on_pass_complete(result)

    return tuple(results)

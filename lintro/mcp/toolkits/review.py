"""The ``lintro_review`` MCP tool.

The tool is a protocol surface over the existing AI diff review engine
(:mod:`lintro.ai.review`): it collects the same git context ``lintro review``
collects, runs the same orchestrator, and serializes the same
:class:`~lintro.ai.review.models.review_result.ReviewResult`. Nothing about the
review itself is reimplemented here, so an agent and the CLI cannot drift apart.

Three things are deliberately different from the CLI:

* **No side effects.** ``--post`` (GitHub commenting) is not exposed. The
  calling agent owns whatever it does with the findings, which is why the tool
  can be annotated read-only. It is *not* idempotent: every call spends money.
  The one thing a review can put on disk is an AI transcript, and only when the
  *operator* opted in with ``ai.transcript_logging`` or
  ``LINTRO_AI_TRANSCRIPT=1``: it lands in the gitignored ``.lintro-cache/``
  rather than the reviewed tree, in the same class as the run-log directory
  :mod:`lintro.mcp.toolkits.runner` redirects. No tool argument can turn it on,
  so an agent cannot make the server write anything.
* **Cost is capped from the server side.** ``ai.max_cost_usd`` in the
  workspace's ``.lintro-config.yaml`` is the ceiling. The ``max_cost_usd``
  argument can only *lower* it — a larger value is clamped, never honored — so
  a misbehaving agent cannot raise its own spend limit.
* **Unavailability is data, not a missing tool.** Without the ``[ai]`` extra, a
  usable provider, or ``ai.review: true``, the tool is still listed and returns
  a ``tool_unavailable`` envelope. An agent gets a reason it can report instead
  of silently losing a capability mid-session.

Latency: a depth-3 review issues several provider calls per chunk and routinely
runs for minutes. The MCP SDK's progress notifications are not used — the
foundation dispatches synchronous handlers into a worker thread with no access
to the in-flight request context, so emitting them would mean threading the
server session through every toolkit. The spec instead carries a much larger
``timeout_seconds`` than the 300s default (see :data:`REVIEW_TIMEOUT_SECONDS`),
and callers should expect a single long-running call.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from lintro.mcp.enums.mcp_error_code import McpErrorCode
from lintro.mcp.errors import McpError, McpErrorEnvelope
from lintro.mcp.registry import McpToolSpec
from lintro.mcp.toolkits.runner import workspace_session

if TYPE_CHECKING:
    from lintro.ai.config import AIConfig
    from lintro.ai.review.models.review_context import ReviewContext
    from lintro.ai.review.models.review_finding import ReviewFinding
    from lintro.ai.review.models.review_metadata import ReviewMetadata
    from lintro.ai.review.models.review_result import ReviewResult

__all__ = ["REVIEW_TIMEOUT_SECONDS", "STRICTNESS_VALUES", "build_review_toolkit"]

# A depth-3 review of a large diff runs for minutes, so the foundation's 300s
# default would abort healthy work. The budget still exists: a wedged provider
# call must not hold the JSON-RPC stream open forever.
REVIEW_TIMEOUT_SECONDS: Final[float] = 1800.0

# Mirrors ``ReviewStrictness``. Spelled out rather than imported so this module
# stays free of AI imports at import time; ``test_toolkit_review`` asserts the
# two never drift.
STRICTNESS_VALUES: Final[tuple[str, ...]] = ("focused", "balanced", "thorough")

_INPUT_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "base": {
            "type": "string",
            "description": (
                "Base git ref to diff against. Defaults to the repository "
                "default branch (origin/HEAD). Cannot be combined with "
                "uncommitted."
            ),
        },
        "uncommitted": {
            "type": "boolean",
            "default": False,
            "description": (
                "Review staged and unstaged working-tree changes instead of a "
                "branch diff. Untracked files are excluded."
            ),
        },
        "depth": {
            "type": "integer",
            "minimum": 1,
            "maximum": 3,
            "description": (
                "Review depth: 1 = checklist, 2 = plus generated questions, "
                "3 = plus an adversarial pass. Higher depths cost more and take "
                "longer. Defaults to review.depth in the workspace config."
            ),
        },
        "strictness": {
            "type": "string",
            "enum": list(STRICTNESS_VALUES),
            "description": (
                "Sensitivity preset: focused (merge blockers only), balanced, "
                "or thorough. Defaults to review.strictness in the config."
            ),
        },
        "with_lint": {
            "type": "boolean",
            "default": False,
            "description": (
                "Run lintro's deterministic linters on the changed files and "
                "include a digest of their findings in the review prompt."
            ),
        },
        "paths": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Limit the review to these path prefixes, relative to the "
                "workspace root or absolute within it. Defaults to the whole diff."
            ),
        },
        "max_cost_usd": {
            "type": "number",
            "exclusiveMinimum": 0,
            "description": (
                "Spend ceiling for this review in USD. Clamped to the "
                "server-side ai.max_cost_usd when that is lower; it can never "
                "raise the configured ceiling."
            ),
        },
    },
    "additionalProperties": False,
}

_DESCRIPTION: Final[str] = (
    "Run lintro's AI diff review over the workspace's git changes and return "
    "structured findings (file, line, severity, category, title, body, "
    "confidence) plus run metadata (model, cost, duration, chunks). Read-only: "
    "nothing is written and nothing is posted to GitHub. Not idempotent — each "
    "call issues provider calls and costs money — and a depth-3 review can run "
    "for several minutes. Requires the [ai] extra, a configured provider, and "
    "ai.review: true; otherwise it returns a tool_unavailable error."
)

# Review context failures that describe the caller's request rather than the
# environment. Everything else is an environment problem or a genuine crash.
_INVALID_INPUT_CONTEXT_CODES: Final[frozenset[str]] = frozenset(
    {
        "default-branch-unknown",
        "merge-base-failed",
        "invalid-review-mode",
        "diff-desync",
        "no-parseable-diff",
    },
)

# Review context failures meaning this workspace cannot serve reviews at all.
_UNAVAILABLE_CONTEXT_CODES: Final[frozenset[str]] = frozenset(
    {
        "git-unavailable",
        "not-git-repo",
        "gh-unavailable",
        "bash-unavailable",
    },
)

_NO_CHANGES_CODE: Final[str] = "no-changes"


@dataclass(frozen=True)
class _BudgetPolicy:
    """The spend ceiling a single review call runs under.

    Attributes:
        requested_usd: The ``max_cost_usd`` argument, or ``None``.
        configured_usd: ``ai.max_cost_usd`` from the workspace config, or
            ``None`` when the operator set no ceiling.
        effective_usd: The ceiling actually handed to the review engine.
        clamped: True when the request was larger than the configured ceiling
            and was lowered to it.
    """

    requested_usd: float | None
    configured_usd: float | None
    effective_usd: float | None
    clamped: bool

    def to_dict(self, *, exceeded: bool) -> dict[str, Any]:
        """Serialize the policy for the tool payload.

        Args:
            exceeded: Whether the run actually stopped on this ceiling.

        Returns:
            dict[str, Any]: The ``budget`` block of the tool result.
        """
        return {
            "requested_usd": self.requested_usd,
            "configured_usd": self.configured_usd,
            "effective_usd": self.effective_usd,
            "clamped": self.clamped,
            "exceeded": exceeded,
        }


def resolve_budget_policy(
    *,
    requested: float | None,
    configured: float | None,
) -> _BudgetPolicy:
    """Combine the requested and configured spend ceilings.

    The configured ceiling wins whenever it is the smaller of the two, so an
    argument can only ever tighten the operator's limit. When the operator set
    no ceiling, the argument becomes the ceiling; when neither is set the review
    runs uncapped, exactly as the CLI does.

    Args:
        requested: The call's ``max_cost_usd`` argument, if any.
        configured: ``ai.max_cost_usd`` from the workspace config, if any.

    Returns:
        _BudgetPolicy: The resolved ceiling and how it was reached.
    """
    if requested is None:
        effective = configured
    elif configured is None:
        effective = requested
    else:
        effective = min(requested, configured)
    return _BudgetPolicy(
        requested_usd=requested,
        configured_usd=configured,
        effective_usd=effective,
        clamped=(
            requested is not None and configured is not None and requested > configured
        ),
    )


def _relative_paths(*, arguments: dict[str, Any], workspace: Path) -> list[str] | None:
    """Return the ``paths`` argument as workspace-relative prefixes.

    The server has already resolved each entry to an absolute, in-workspace path
    (``paths`` is declared in ``path_arguments``), but the review context filters
    on repository-relative prefixes, so the absolute forms are mapped back.

    Args:
        arguments: Validated tool arguments.
        workspace: Workspace root.

    Returns:
        list[str] | None: Relative path prefixes, or ``None`` for the whole diff.

    Raises:
        McpError: :attr:`McpErrorCode.INVALID_INPUT` when a path cannot be
            expressed relative to the workspace root.
    """
    raw = arguments.get("paths") or []
    if not raw:
        return None
    relative: list[str] = []
    for entry in raw:
        candidate = Path(str(entry))
        try:
            relative.append(str(candidate.relative_to(workspace)))
        except ValueError as exc:
            raise McpError(
                code=McpErrorCode.INVALID_INPUT,
                message=f"Path is not relative to the workspace: {entry}",
                detail={"path": str(entry), "workspace": str(workspace)},
            ) from exc
    return relative


def _resolve_ai_config(*, workspace: Path) -> tuple[Any, AIConfig]:
    """Load the workspace config and require AI review to be usable.

    Args:
        workspace: Workspace root, used only for the error detail.

    Returns:
        tuple[Any, AIConfig]: The Lintro config and its resolved AI section.

    Raises:
        McpError: :attr:`McpErrorCode.TOOL_UNAVAILABLE` when no AI provider is
            installed or ``ai.review`` is disabled for this workspace.
    """
    from lintro.ai.availability import is_ai_available
    from lintro.ai.interface import resolve_ai_config
    from lintro.config.config_loader import get_config

    if not is_ai_available():
        raise McpError(
            code=McpErrorCode.TOOL_UNAVAILABLE,
            message=(
                "AI review requires the lintro[ai] extra and a configured "
                "provider. Install with: uv pip install 'lintro[ai]'"
            ),
            detail={"tool": "lintro_review", "reason": "ai_unavailable"},
        )

    lintro_config = get_config()
    ai_config = resolve_ai_config(lintro_config)
    if not ai_config.review_enabled:
        raise McpError(
            code=McpErrorCode.TOOL_UNAVAILABLE,
            message=(
                "AI review is disabled for this workspace. Set ai.enabled: true "
                "and ai.review: true in .lintro-config.yaml"
            ),
            detail={
                "tool": "lintro_review",
                "reason": "review_disabled",
                "workspace": str(workspace),
            },
        )
    return lintro_config, ai_config


def _collect_context(
    *,
    arguments: dict[str, Any],
    workspace: Path,
) -> ReviewContext | None:
    """Collect the git diff context for this call.

    Args:
        arguments: Validated tool arguments.
        workspace: Workspace root.

    Returns:
        ReviewContext | None: The collected context, or ``None`` when the diff
        is empty — "nothing changed" is an answer, not a failure.

    Raises:
        McpError: With :attr:`McpErrorCode.INVALID_INPUT` for a bad ``base`` or
            an unusable diff request, :attr:`McpErrorCode.TOOL_UNAVAILABLE` when
            git itself is missing, and :attr:`McpErrorCode.EXECUTION_ERROR`
            otherwise.
    """
    from lintro.ai.review import collect_review_context
    from lintro.ai.review.exceptions import ReviewContextError

    try:
        return collect_review_context(
            base=arguments.get("base"),
            uncommitted=bool(arguments.get("uncommitted", False)),
            paths=_relative_paths(arguments=arguments, workspace=workspace),
        )
    except ReviewContextError as exc:
        code = str(exc.code.value)
        if code == _NO_CHANGES_CODE:
            return None
        if code in _UNAVAILABLE_CONTEXT_CODES:
            mcp_code = McpErrorCode.TOOL_UNAVAILABLE
        elif code in _INVALID_INPUT_CONTEXT_CODES:
            mcp_code = McpErrorCode.INVALID_INPUT
        else:
            mcp_code = McpErrorCode.EXECUTION_ERROR
        raise McpError(
            code=mcp_code,
            message=str(exc),
            detail={"tool": "lintro_review", "context_error": code},
        ) from exc


def _lint_digest(
    *,
    context: ReviewContext,
    lintro_config: Any,
) -> str | None:
    """Build the ``with_lint`` digest for the review prompt.

    Args:
        context: Collected review context.
        lintro_config: Loaded Lintro configuration.

    Returns:
        str | None: A compact lint digest, or ``None`` when there is nothing
        to report.
    """
    from lintro.ai.review.lint_bridge import (
        format_lint_results_for_prompt,
        run_lint_on_changed_files,
    )

    results = run_lint_on_changed_files(
        changed_files=[file.path for file in context.changed_files],
        lintro_config=lintro_config,
    )
    return format_lint_results_for_prompt(results=results) or None


def _finding_body(*, finding: ReviewFinding) -> str:
    """Render a finding's prose into one ``body`` field.

    The review model splits its prose across ``description``, ``cause``, and
    ``fix``; the MCP contract carries a single labelled body so an agent can
    quote it verbatim without knowing the review model's shape.

    Args:
        finding: The review finding.

    Returns:
        str: The composed body text.
    """
    sections = (
        ("", finding.description),
        ("Cause: ", finding.cause),
        ("Fix: ", finding.fix),
    )
    return "\n\n".join(
        f"{label}{text.strip()}" for label, text in sections if text.strip()
    )


def _finding_to_dict(*, finding: ReviewFinding) -> dict[str, Any]:
    """Serialize one review finding for the tool payload.

    Args:
        finding: The review finding.

    Returns:
        dict[str, Any]: The finding as the MCP contract carries it.
    """
    return {
        "file": finding.file,
        "line": finding.line,
        "severity": str(finding.severity.value),
        "category": finding.category,
        "title": finding.title,
        "body": _finding_body(finding=finding),
        "confidence": finding.confidence,
        "suggested_code": finding.suggested_code,
        "checklist_ids": list(finding.checklist_ids),
        "source": finding.source,
    }


def _run_metadata(*, metadata: ReviewMetadata) -> dict[str, Any]:
    """Serialize review run metadata for the tool payload.

    Args:
        metadata: Metadata from the completed review.

    Returns:
        dict[str, Any]: The ``run`` block of the tool result.
    """
    return {
        "model": metadata.model,
        "provider": metadata.provider,
        "depth": metadata.depth,
        "strictness": metadata.strictness,
        "cost_usd": metadata.cost_estimate_usd,
        "duration_seconds": metadata.duration_seconds,
        "chunks": {
            "total": metadata.chunks_total,
            "reviewed": metadata.chunks_reviewed,
        },
        "files": {
            "reviewed": metadata.files_reviewed,
            "total": metadata.files_total,
        },
        "token_usage": dict(metadata.token_usage),
        "token_usage_estimated": metadata.token_usage_estimated,
        "base_ref": metadata.base_ref,
        "head_ref": metadata.head_ref,
        "timestamp": metadata.timestamp,
        "partial": metadata.partial,
        "stopped_reason": metadata.stopped_reason,
    }


def _stopped_on_budget(*, metadata: ReviewMetadata) -> bool:
    """Return whether the run stopped early because of the spend ceiling.

    Args:
        metadata: Metadata from the completed review.

    Returns:
        bool: True when the review engine ended the run on its cost cap.
    """
    return metadata.partial and "cost cap" in metadata.stopped_reason.lower()


def _review_payload(
    *,
    result: ReviewResult,
    budget: _BudgetPolicy,
) -> dict[str, Any]:
    """Shape a completed review as the tool result.

    Args:
        result: The review the orchestrator produced.
        budget: The ceiling the run was given.

    Returns:
        dict[str, Any]: Findings, run metadata, and the budget block.

    Raises:
        McpError: :attr:`McpErrorCode.BUDGET_EXCEEDED` when the ceiling stopped
            the run before a single chunk was reviewed, so the call produced no
            review at all rather than a shorter one.
    """
    exceeded = _stopped_on_budget(metadata=result.metadata)
    if exceeded and result.metadata.chunks_reviewed == 0:
        raise McpError(
            code=McpErrorCode.BUDGET_EXCEEDED,
            message=(
                "AI cost budget exhausted before any chunk was reviewed: "
                f"{result.metadata.stopped_reason}"
            ),
            detail={
                "tool": "lintro_review",
                "budget": budget.to_dict(exceeded=True),
                "chunks_total": result.metadata.chunks_total,
                "cost_usd": result.metadata.cost_estimate_usd,
            },
        )
    return {
        "summary": result.summary,
        "findings": [_finding_to_dict(finding=finding) for finding in result.findings],
        "run": _run_metadata(metadata=result.metadata),
        "budget": budget.to_dict(exceeded=exceeded),
    }


def _no_changes_payload(
    *,
    ai_config: AIConfig,
    depth: int,
    strictness: str,
    budget: _BudgetPolicy,
) -> dict[str, Any]:
    """Build the result for a diff with nothing in it.

    The metadata is assembled from the same :class:`ReviewMetadata` model a real
    run produces, so the empty answer and a reviewed one carry an identical key
    set and an agent needs no special case beyond ``findings == []``.

    Args:
        ai_config: Resolved AI configuration.
        depth: The depth the call asked for.
        strictness: The strictness the call asked for.
        budget: The ceiling the call would have run under.

    Returns:
        dict[str, Any]: A zero-cost, zero-finding review result.
    """
    from lintro.ai.review.models.review_metadata import ReviewMetadata
    from lintro.ai.review.models.review_result import ReviewResult

    metadata = ReviewMetadata(
        model=ai_config.model or "",
        provider=str(ai_config.provider),
        context_window=0,
        depth=depth,
        chunks_total=0,
        chunks_current=0,
        files_reviewed=0,
        files_total=0,
        checklist_items=0,
        strictness=strictness,
    )
    result = ReviewResult(metadata=metadata, summary="No changes to review.")
    return {
        "summary": result.summary,
        "findings": [],
        "run": _run_metadata(metadata=result.metadata),
        "budget": budget.to_dict(exceeded=False),
    }


def _review_failure(*, provider_name: str, error: Exception) -> McpErrorEnvelope:
    """Translate a failed review into a structured MCP error envelope.

    Args:
        provider_name: Provider identifier for the error taxonomy.
        error: The exception that aborted the review.

    Returns:
        McpErrorEnvelope: The envelope to raise, carrying the review error
        contract's own ``kind``/``status``/``retryable`` fields as detail so an
        agent gets the same diagnosis ``lintro review --output json`` prints.
    """
    from lintro.ai.review.error_contract import build_error_contract

    contract = build_error_contract(provider=provider_name, error=error)
    body = dict(contract["error"])
    code = (
        McpErrorCode.TOOL_UNAVAILABLE
        if body.get("provider_unavailable")
        else McpErrorCode.EXECUTION_ERROR
    )
    return McpErrorEnvelope(
        code=code,
        message=str(body.get("message") or error) or error.__class__.__name__,
        detail={"tool": "lintro_review", "review_error": body},
    )


def _execute_review(*, arguments: dict[str, Any], workspace: Path) -> dict[str, Any]:
    """Run one review end to end inside an open workspace session.

    Args:
        arguments: Validated tool arguments.
        workspace: Workspace root the review is anchored to.

    Returns:
        dict[str, Any]: The tool result payload.

    Raises:
        McpError: For every failure mode the tool contract defines.
    """
    from lintro.ai.exceptions import AIError
    from lintro.ai.providers import get_provider
    from lintro.ai.review import (
        classify_changed_files,
        format_checklist_for_prompt,
        get_all_checklist_items,
        select_checklist_items,
    )
    from lintro.ai.review.enums.review_strictness import ReviewStrictness
    from lintro.ai.review.orchestrator import run_review
    from lintro.ai.review.sensitivity import resolve_sensitivity_policy

    lintro_config, ai_config = _resolve_ai_config(workspace=workspace)
    budget = resolve_budget_policy(
        requested=arguments.get("max_cost_usd"),
        configured=ai_config.max_cost_usd,
    )
    effective_ai_config = ai_config.model_copy(
        update={"max_cost_usd": budget.effective_usd},
    )
    depth = int(arguments.get("depth") or lintro_config.review.depth)
    strictness = ReviewStrictness(
        str(
            arguments.get("strictness") or lintro_config.review.strictness.value,
        ).lower(),
    )

    context = _collect_context(arguments=arguments, workspace=workspace)
    if context is None:
        return _no_changes_payload(
            ai_config=ai_config,
            depth=depth,
            strictness=strictness.value,
            budget=budget,
        )

    classifications = classify_changed_files(context.changed_files)
    selected_items = select_checklist_items(
        classifications=classifications,
        items=get_all_checklist_items(config=lintro_config),
    )
    checklist_text, _prompt_mapping = format_checklist_for_prompt(items=selected_items)
    lint_results = (
        _lint_digest(context=context, lintro_config=lintro_config)
        if arguments.get("with_lint", False)
        else None
    )

    try:
        provider = get_provider(effective_ai_config, workspace_root=workspace)
    except ValueError as exc:
        raise McpError(
            code=McpErrorCode.TOOL_UNAVAILABLE,
            message=str(exc),
            detail={"tool": "lintro_review", "reason": "provider_unavailable"},
        ) from exc

    try:
        result = run_review(
            context,
            provider=provider,
            ai_config=effective_ai_config,
            depth=depth,
            checklist_items=selected_items,
            checklist_text=checklist_text,
            classifications=classifications,
            lint_results=lint_results,
            sensitivity=resolve_sensitivity_policy(
                strictness=strictness,
                overrides=lintro_config.review.sensitivity,
            ),
            force_semantic_chunking=lintro_config.review.force_semantic_chunking,
            workspace_root=workspace,
        )
    except (AIError, ValueError) as exc:
        failure = _review_failure(provider_name=str(provider.name), error=exc)
        raise McpError(
            code=failure.code,
            message=failure.message,
            detail=failure.detail,
        ) from exc
    return _review_payload(result=result, budget=budget)


def _review_handler(
    *,
    workspace: Path,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Build the ``lintro_review`` handler bound to ``workspace``.

    Args:
        workspace: Workspace root the review is anchored to.

    Returns:
        Callable: A handler accepting an arguments dict.
    """

    def handler(arguments: dict[str, Any]) -> dict[str, Any]:
        # The session anchors cwd (git, the config file, and the provider's CLI
        # transport all resolve relative to it) and serializes the call against
        # the lint tools, which mutate the same process-global state. A review
        # therefore holds the server's run lock for its whole, possibly
        # multi-minute duration.
        with workspace_session(workspace=workspace):
            return _execute_review(arguments=arguments, workspace=workspace)

    return handler


def build_review_toolkit(*, workspace: Path) -> tuple[McpToolSpec, ...]:
    """Build the review tool specification.

    Args:
        workspace: Workspace root the tool operates on.

    Returns:
        tuple[McpToolSpec, ...]: The ``lintro_review`` tool.
    """
    return (
        McpToolSpec(
            name="lintro_review",
            description=_DESCRIPTION,
            input_schema=_INPUT_SCHEMA,
            handler=_review_handler(workspace=workspace),
            read_only=True,
            destructive=False,
            # Every call issues provider calls and spends money, so repeating
            # one is never free even though the workspace is untouched.
            idempotent=False,
            timeout_seconds=REVIEW_TIMEOUT_SECONDS,
            path_arguments=("paths",),
        ),
    )

"""Characterization locks for #1972 Phase 1 architecture seams.

These tests freeze *current* CLI/MCP review preparation behavior,
effective-config parity, review metadata shape, error mapping, and exit
semantics (0/1/2) before later phases extract shared preparation or split the
orchestrator. Issue #1970 landed ``ResolvedAIConfig``; Phase 3 still owns
``prepare_review``, orchestrator decomposition, and provider close wiring.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from assertpy import assert_that
from click.testing import CliRunner

from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport
from lintro.ai.exceptions import AIAuthenticationError
from lintro.ai.interface import resolve_ai_config
from lintro.ai.review.enums.checklist_display import ChecklistDisplay
from lintro.ai.review.enums.review_strictness import ReviewStrictness
from lintro.ai.review.error_contract import (
    REVIEW_ERROR_EXIT_CODE,
    build_error_contract,
)
from lintro.ai.review.models.review_finding import ReviewFinding, Severity
from lintro.ai.review.models.review_metadata import ReviewMetadata
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.transport import apply_transport_override
from lintro.cli import cli
from lintro.config.lintro_config import LintroConfig
from lintro.mcp.toolkits import review as mcp_review
from lintro.mcp.toolkits.review import resolve_budget_policy

PROJECT_ROOT = Path(__file__).resolve().parents[4]
CLI_REVIEW_PATH = PROJECT_ROOT / "lintro/cli_utils/commands/review.py"
MCP_REVIEW_PATH = PROJECT_ROOT / "lintro/mcp/toolkits/review.py"

# Domain helpers both adapters must keep calling until Phase 3 extracts them.
_SHARED_PREPARATION_CALLS: frozenset[str] = frozenset(
    {
        "collect_review_context",
        "classify_changed_files",
        "get_all_checklist_items",
        "select_checklist_items",
        "format_checklist_for_prompt",
        "get_provider",
        "run_review",
        "resolve_sensitivity_policy",
    },
)

# MCP ``run`` payload keys produced by ``_run_metadata`` today.
_MCP_RUN_METADATA_KEYS: frozenset[str] = frozenset(
    {
        "model",
        "provider",
        "depth",
        "strictness",
        "cost_usd",
        "duration_seconds",
        "phase_timings",
        "chunks",
        "files",
        "token_usage",
        "token_usage_estimated",
        "base_ref",
        "head_ref",
        "timestamp",
        "partial",
        "stopped_reason",
    },
)


def _called_names(path: Path) -> set[str]:
    """Return function/attribute names invoked in ``path``.

    Args:
        path: Python source file.

    Returns:
        Set of called names (``Name`` or ``Attribute.attr``).
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            names.add(func.id)
        elif isinstance(func, ast.Attribute):
            names.add(func.attr)
    return names


def _metadata(**overrides: Any) -> ReviewMetadata:
    """Build review metadata for characterization stubs.

    Args:
        **overrides: Field overrides.

    Returns:
        ReviewMetadata instance.
    """
    payload: dict[str, Any] = {
        "model": "gpt-4o",
        "provider": "openai",
        "context_window": 128_000,
        "depth": 1,
        "chunks_total": 1,
        "chunks_current": 1,
        "files_reviewed": 1,
        "files_total": 1,
        "checklist_items": 0,
        "cost_estimate_usd": 0.1,
        "duration_seconds": 1.5,
        "strictness": "balanced",
        "chunks_reviewed": 1,
    }
    payload.update(overrides)
    return ReviewMetadata(**payload)


def _result_with_findings(*findings: ReviewFinding) -> ReviewResult:
    """Build a ReviewResult carrying the given findings.

    Args:
        *findings: Findings to attach.

    Returns:
        ReviewResult for CLI exit-code stubs.
    """
    return ReviewResult(
        metadata=_metadata(),
        summary="characterization",
        findings=findings,
    )


def _p1_finding() -> ReviewFinding:
    """Return one blocking finding."""
    return ReviewFinding(
        severity=Severity.P1,
        category="correctness",
        file="app.py",
        line=1,
        title="Blocking issue",
        description="Breaks production.",
        cause="Missing guard.",
        fix="Add the guard.",
        confidence="high",
    )


def _p2_finding() -> ReviewFinding:
    """Return one non-blocking finding."""
    return ReviewFinding(
        severity=Severity.P2,
        category="style",
        file="app.py",
        line=2,
        title="Non-blocking issue",
        description="Cosmetic.",
        cause="Naming.",
        fix="Rename.",
        confidence="medium",
    )


def _invoke_review(*, run_review_return: ReviewResult) -> Any:
    """Invoke ``lintro review`` with collaborators stubbed through ``run_review``.

    Args:
        run_review_return: Stubbed orchestrator result.

    Returns:
        CliRunner result.
    """
    runner = CliRunner()
    mock_context = MagicMock()
    mock_context.changed_files = []
    mock_context.unified_diff = ""
    mock_config = MagicMock(ai={"enabled": True, "review": True})
    mock_config.review.depth = 1
    mock_config.review.strictness = ReviewStrictness.BALANCED
    mock_config.review.sensitivity = MagicMock()
    mock_config.review.force_semantic_chunking = False
    mock_config.review.checklist_display = ChecklistDisplay.OFF
    mock_config.review.custom_agents = MagicMock()

    with (
        patch("lintro.cli_utils.commands.review.require_ai"),
        patch(
            "lintro.cli_utils.commands.review.get_config",
            return_value=mock_config,
        ),
        patch(
            "lintro.cli_utils.commands.review.collect_review_context",
            return_value=mock_context,
        ),
        patch(
            "lintro.cli_utils.commands.review.classify_changed_files",
            return_value=[],
        ),
        patch(
            "lintro.cli_utils.commands.review.get_all_checklist_items",
            return_value=[],
        ),
        patch(
            "lintro.cli_utils.commands.review.select_checklist_items",
            return_value=[],
        ),
        patch(
            "lintro.cli_utils.commands.review.format_checklist_for_prompt",
            return_value=("", {}),
        ),
        patch(
            "lintro.cli_utils.commands.review.get_provider",
            return_value=MagicMock(model_name="gpt-4o", name="openai"),
        ),
        patch(
            "lintro.cli_utils.commands.review.run_review",
            return_value=run_review_return,
        ),
        patch("lintro.cli_utils.commands.review.render_review_output"),
        patch(
            "lintro.cli_utils.commands.review._execute_advisory",
            return_value=[],
        ),
    ):
        return runner.invoke(cli, ["review"])


# ---------------------------------------------------------------------------
# Shared preparation call set
# ---------------------------------------------------------------------------


def test_cli_and_mcp_review_adapters_call_shared_preparation_helpers() -> None:
    """CLI and MCP both invoke the domain helpers Phase 3 will extract."""
    cli_calls = _called_names(CLI_REVIEW_PATH)
    mcp_calls = _called_names(MCP_REVIEW_PATH)

    assert_that(_SHARED_PREPARATION_CALLS.issubset(cli_calls)).is_true()
    assert_that(_SHARED_PREPARATION_CALLS.issubset(mcp_calls)).is_true()
    # CLI keeps provenance via resolve_from_mapping (#1970); MCP still goes
    # through resolve_ai_config, which applies the same env layer internally.
    assert_that(cli_calls).contains("resolve_from_mapping")
    assert_that(cli_calls).contains("apply_cli_overrides")
    assert_that(mcp_calls).contains("resolve_ai_config")


def test_cli_owns_posting_and_exit_helpers_mcp_does_not() -> None:
    """Adapter policy stays split: posting/exit on CLI, budget clamp on MCP."""
    cli_source = CLI_REVIEW_PATH.read_text(encoding="utf-8")
    mcp_source = MCP_REVIEW_PATH.read_text(encoding="utf-8")

    assert_that(cli_source).contains("post_review_to_github")
    assert_that(cli_source).contains("REVIEW_ERROR_EXIT_CODE")
    assert_that(cli_source).contains("SystemExit")
    assert_that(mcp_source).does_not_contain("post_review_to_github")
    assert_that(mcp_source).contains("resolve_budget_policy")
    assert_that(cli_source).does_not_contain("resolve_budget_policy")


# ---------------------------------------------------------------------------
# Effective-config parity
# ---------------------------------------------------------------------------


def test_resolve_ai_config_is_the_single_typed_ai_seam() -> None:
    """Identical raw ``ai:`` mappings resolve identically for every surface."""
    raw = {
        "enabled": True,
        "review": True,
        "provider": "anthropic",
        "model": "claude-sonnet-4-20250514",
        "transport": "api",
        "max_cost_usd": 1.25,
    }
    config = LintroConfig(ai=raw)

    resolved = resolve_ai_config(config)

    assert_that(resolved).is_equal_to(AIConfig.from_mapping(raw))
    assert_that(resolved.review_enabled).is_true()
    assert_that(resolved.max_cost_usd).is_equal_to(1.25)


def test_cli_transport_override_does_not_mutate_base_config() -> None:
    """CLI transport override copies config; base resolution stays untouched."""
    base = AIConfig(enabled=True, review=True, transport=AITransport.API)

    overridden = apply_transport_override(base, "cli")

    assert_that(base.transport).is_equal_to(AITransport.API)
    assert_that(overridden.transport).is_equal_to(AITransport.CLI)
    assert_that(overridden is base).is_false()


def test_mcp_budget_policy_is_monotonic_never_raises_cap() -> None:
    """MCP may lower ``max_cost_usd`` per call but never raise the config cap."""
    raised = resolve_budget_policy(requested=50.0, configured=1.0)
    lowered = resolve_budget_policy(requested=0.25, configured=1.0)
    absent = resolve_budget_policy(requested=None, configured=1.0)

    assert_that(raised.effective_usd).is_equal_to(1.0)
    assert_that(raised.clamped).is_true()
    assert_that(lowered.effective_usd).is_equal_to(0.25)
    assert_that(lowered.clamped).is_false()
    assert_that(absent.effective_usd).is_equal_to(1.0)


# ---------------------------------------------------------------------------
# Review metadata
# ---------------------------------------------------------------------------


def test_mcp_run_metadata_exposes_stable_key_set() -> None:
    """MCP ``run`` metadata keeps the key set agents already consume."""
    metadata = _metadata(
        token_usage={"prompt": 1, "completion": 1, "total": 2},
        base_ref="main",
        head_ref="HEAD",
        timestamp="2026-08-08T00:00:00+00:00",
        partial=False,
        stopped_reason="",
        token_usage_estimated=True,
    )

    payload = mcp_review._run_metadata(metadata=metadata)

    assert_that(set(payload)).is_equal_to(_MCP_RUN_METADATA_KEYS)
    assert_that(payload["chunks"]).is_equal_to({"total": 1, "reviewed": 1})
    assert_that(payload["files"]).is_equal_to({"reviewed": 1, "total": 1})
    assert_that(payload["cost_usd"]).is_equal_to(0.1)
    assert_that(payload["token_usage_estimated"]).is_true()


def test_review_metadata_fields_remain_frozen_contract() -> None:
    """``ReviewMetadata`` field names stay the provenance projection surface."""
    field_names = {field.name for field in ReviewMetadata.__dataclass_fields__.values()}

    assert_that(field_names).contains(
        "model",
        "provider",
        "depth",
        "strictness",
        "cost_estimate_usd",
        "duration_seconds",
        "chunks_total",
        "chunks_reviewed",
        "files_reviewed",
        "files_total",
        "token_usage",
        "token_usage_estimated",
        "partial",
        "stopped_reason",
        "base_ref",
        "head_ref",
        "timestamp",
    )


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


def test_mcp_review_failure_reuses_cli_error_contract_fields() -> None:
    """MCP failure envelopes carry the same diagnosis fields as CLI JSON."""
    error = AIAuthenticationError(
        "Anthropic authentication failed: Error code: 401 - authentication_error",
    )
    contract = build_error_contract(provider="anthropic", error=error)
    envelope = mcp_review._review_failure(provider_name="anthropic", error=error)

    assert_that((envelope.detail or {})["review_error"]).is_equal_to(
        contract["error"],
    )
    assert_that(contract["error"]).contains_key(
        "kind",
        "provider",
        "status",
        "retryable",
        "provider_unavailable",
        "message",
    )


def test_review_error_exit_code_stays_two_distinct_from_p1() -> None:
    """Exit 2 means no review; exit 1 stays reserved for successful P1 findings."""
    assert_that(REVIEW_ERROR_EXIT_CODE).is_equal_to(2)
    assert_that(REVIEW_ERROR_EXIT_CODE).is_not_equal_to(1)


# ---------------------------------------------------------------------------
# Exit behavior 0 / 1 / 2
# ---------------------------------------------------------------------------


def test_review_exit_zero_for_successful_review_without_p1() -> None:
    """Successful review with only P2 findings exits 0."""
    result = _invoke_review(run_review_return=_result_with_findings(_p2_finding()))

    assert_that(result.exit_code).is_equal_to(0)


def test_review_exit_one_for_successful_review_with_p1() -> None:
    """Successful review that produced P1 findings exits 1."""
    result = _invoke_review(run_review_return=_result_with_findings(_p1_finding()))

    assert_that(result.exit_code).is_equal_to(1)
    assert_that(result.exception).is_instance_of(SystemExit)


def test_review_exit_two_when_orchestrator_raises() -> None:
    """Provider/execution failure exits 2 so it is never confused with P1.

    ``_execute_advisory`` is deliberately left unpatched: ``run_review``
    raises first, and the command's exception handler exits before the
    advisory pass is reached. If that ordering ever changes, this test
    starts exercising the real advisory path and should gain a stub.
    """
    runner = CliRunner()
    mock_context = MagicMock()
    mock_context.changed_files = []
    mock_config = MagicMock(ai={"enabled": True, "review": True})
    mock_config.review.depth = 1
    mock_config.review.strictness = ReviewStrictness.BALANCED
    mock_config.review.sensitivity = MagicMock()
    mock_config.review.force_semantic_chunking = False
    mock_config.review.checklist_display = ChecklistDisplay.OFF

    with (
        patch("lintro.cli_utils.commands.review.require_ai"),
        patch(
            "lintro.cli_utils.commands.review.get_config",
            return_value=mock_config,
        ),
        patch(
            "lintro.cli_utils.commands.review.collect_review_context",
            return_value=mock_context,
        ),
        patch(
            "lintro.cli_utils.commands.review.classify_changed_files",
            return_value=[],
        ),
        patch(
            "lintro.cli_utils.commands.review.get_all_checklist_items",
            return_value=[],
        ),
        patch(
            "lintro.cli_utils.commands.review.select_checklist_items",
            return_value=[],
        ),
        patch(
            "lintro.cli_utils.commands.review.format_checklist_for_prompt",
            return_value=("", {}),
        ),
        patch(
            "lintro.cli_utils.commands.review.get_provider",
            return_value=MagicMock(model_name="gpt-4o", name="openai"),
        ),
        patch(
            "lintro.cli_utils.commands.review.run_review",
            side_effect=AIAuthenticationError("401 authentication_error"),
        ),
        patch("lintro.cli_utils.commands.review.render_review_error"),
    ):
        result = runner.invoke(cli, ["review"])

    assert_that(result.exit_code).is_equal_to(REVIEW_ERROR_EXIT_CODE)


def test_run_review_remains_stable_orchestrator_facade() -> None:
    """Phase 4 may move internals, but ``run_review`` stays the public facade."""
    from lintro.ai.review import orchestrator

    assert_that(hasattr(orchestrator, "run_review")).is_true()
    assert_that(hasattr(orchestrator, "run_review_async")).is_true()
    assert_that(inspect.isfunction(orchestrator.run_review)).is_true()
    assert_that("run_review" in orchestrator.__all__).is_true()

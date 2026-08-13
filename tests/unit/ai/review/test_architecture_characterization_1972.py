"""Phase 1 characterization gaps for epic #1972.

Pins behaviours that lacked golden coverage before the effective-config /
shared-prep refactor (Phases 2–3): config-resolution idempotence, the shared
``run_review`` kwarg surface, CLI/MCP error-contract body parity, and MCP
error mapping. Exit-code and metadata key-set behaviour is pinned in
``test_architecture_characterization.py`` and intentionally not repeated
here — see the gap list in
``docs/adr/0006-ai-effective-config-and-review-execution.md``.
"""

from __future__ import annotations

import json
import subprocess  # nosec B404 - fixed git argv against a temp repo
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport
from lintro.ai.exceptions import AIAuthenticationError
from lintro.ai.interface import resolve_ai_config
from lintro.ai.review.enums.review_strictness import ReviewStrictness
from lintro.ai.review.error_contract import (
    build_error_contract,
)
from lintro.ai.review.models.review_finding import ReviewFinding, Severity
from lintro.ai.review.models.review_metadata import ReviewMetadata
from lintro.ai.review.models.review_result import ReviewResult
from lintro.cli import cli
from lintro.config.lintro_config import LintroConfig
from lintro.mcp.enums.mcp_error_code import McpErrorCode
from lintro.mcp.errors import McpError
from lintro.mcp.toolkits import review as mcp_review

# Keys both CLI and MCP currently forward into ``run_review``. Adapter-only
# kwargs (CLI progress / custom agents; MCP has neither) are listed separately
# so Phase 3 can shrink the divergent set without rewriting this golden.
_SHARED_RUN_REVIEW_KWARGS: frozenset[str] = frozenset(
    {
        "provider",
        "ai_config",
        "depth",
        "checklist_items",
        "checklist_text",
        "classifications",
        "lint_results",
        "sensitivity",
        "force_semantic_chunking",
        "workspace_root",
    },
)

_CLI_ONLY_RUN_REVIEW_KWARGS: frozenset[str] = frozenset(
    {
        "context_window_override",
        "progress",
        "custom_agents",
        "run_builtin_checklist",
    },
)


@pytest.fixture(autouse=True)
def _isolate_config_cache() -> Iterator[None]:
    """Clear the global config singleton around every test in this module.

    The workspace-based tests chdir into a temp repo and exercise real
    resolution paths; modules holding a ``from … import get_config`` binding
    bypass the monkeypatch and populate ``config_loader._loaded_config`` with
    the temp workspace's config, which then leaks into unrelated tests (the
    doctor suite reads the poisoned singleton and exits non-zero).

    Yields:
        None: Cache is cleared on both sides of the test body.
    """
    from lintro.config import config_loader

    config_loader.clear_config_cache()
    yield
    config_loader.clear_config_cache()


def _git(*args: str, cwd: Path) -> None:
    """Run one fixed git command in ``cwd``.

    Args:
        *args: Git arguments.
        cwd: Directory to run in.
    """
    subprocess.run(  # nosec B603 B607 - fixed git argv against a temp repo
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def _metadata(*, model: str = "gpt-4o", provider: str = "openai") -> ReviewMetadata:
    """Build minimal review metadata for CLI exit-gate characterization.

    Args:
        model: Model identifier.
        provider: Provider identifier.

    Returns:
        ReviewMetadata for a completed one-chunk run.
    """
    return ReviewMetadata(
        model=model,
        provider=provider,
        context_window=128_000,
        depth=1,
        chunks_total=1,
        chunks_current=1,
        files_reviewed=1,
        files_total=1,
        checklist_items=0,
        strictness=ReviewStrictness.BALANCED.value,
        chunks_reviewed=1,
        duration_seconds=1.0,
        token_usage={"prompt": 1, "completion": 1, "total": 2},
        cost_estimate_usd=0.01,
        base_ref="main",
        head_ref="HEAD",
        timestamp="2026-08-08T00:00:00+00:00",
    )


def _finding(*, severity: Severity) -> ReviewFinding:
    """Build one finding at the given severity.

    Args:
        severity: Finding severity.

    Returns:
        A review finding suitable for exit-gate tests.
    """
    return ReviewFinding(
        severity=severity,
        category="correctness",
        file="app.py",
        line=1,
        title="Issue",
        description="Something is wrong.",
        cause="Root cause.",
        fix="Fix it.",
        confidence="high",
        failure_scenario="It fails in production." if severity is Severity.P1 else "",
    )


def _result_with(*, findings: tuple[ReviewFinding, ...]) -> ReviewResult:
    """Wrap findings in a successful review result.

    Args:
        findings: Findings to attach.

    Returns:
        ReviewResult carrying the findings.
    """
    return ReviewResult(
        metadata=_metadata(),
        summary="Review complete.",
        findings=findings,
    )


def _write_review_workspace(tmp_path: Path) -> Path:
    """Create a git workspace with AI review enabled and one changed file.

    Args:
        tmp_path: Pytest temporary directory.

    Returns:
        Resolved workspace root on a branch ahead of ``main``.
    """
    workspace = tmp_path.resolve()
    (workspace / ".lintro-config.yaml").write_text(
        "ai:\n  enabled: true\n  review: true\n  provider: anthropic\n"
        "  model: test-model\n  max_cost_usd: 1.0\n",
        encoding="utf-8",
    )
    _git("init", "--initial-branch", "main", cwd=workspace)
    _git("config", "user.email", "test@example.com", cwd=workspace)
    _git("config", "user.name", "Test", cwd=workspace)
    (workspace / "app.py").write_text("x = 1\n", encoding="utf-8")
    _git("add", "-A", cwd=workspace)
    _git("commit", "-m", "base", cwd=workspace)
    _git("checkout", "-b", "feature", cwd=workspace)
    (workspace / "app.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
    _git("add", "-A", cwd=workspace)
    _git("commit", "-m", "change", cwd=workspace)
    return workspace


# --- exit behaviour 0 / 1 / 2 -------------------------------------------------


def test_resolve_ai_config_matches_for_identical_raw_mapping() -> None:
    """CLI and MCP both resolve AI config through ``resolve_ai_config``.

    Characterizes today's contract: the same raw ``ai:`` mapping on
    ``LintroConfig`` yields the same ``AIConfig`` regardless of which surface
    asks. Phase 2 must keep this once provenance wraps the value.
    """
    raw = {
        "enabled": True,
        "review": True,
        "lint": False,
        "provider": "anthropic",
        "model": "claude-sonnet-4-20250514",
        "transport": "api",
        "max_cost_usd": 0.5,
        "max_tokens": 4096,
    }
    left = resolve_ai_config(LintroConfig(ai=dict(raw)))
    right = resolve_ai_config(LintroConfig(ai=dict(raw)))

    assert_that(left).is_equal_to(right)
    assert_that(left).is_equal_to(AIConfig.from_mapping(raw))
    assert_that(left.review_enabled).is_true()
    assert_that(left.transport).is_equal_to(AITransport.API)
    assert_that(left.max_cost_usd).is_equal_to(0.5)


# --- CLI / MCP review preparation parity --------------------------------------


def test_cli_and_mcp_pass_the_same_shared_run_review_kwargs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shared preparation fields reach ``run_review`` from both surfaces.

    Adapter-only kwargs are recorded so Phase 3 can extract shared prep without
    inventing parity that does not exist today (CLI progress/custom agents;
    MCP budget clamp on ``ai_config.max_cost_usd``).
    """
    import lintro.ai.availability as availability
    import lintro.ai.providers as providers
    import lintro.ai.review.orchestrator as orchestrator
    from lintro.config import config_loader

    workspace = _write_review_workspace(tmp_path)
    monkeypatch.chdir(workspace)
    monkeypatch.setattr(availability, "is_ai_available", lambda: True)

    class _FakeProvider:
        name = "anthropic"
        model_name = "test-model"

    monkeypatch.setattr(
        providers,
        "get_provider",
        lambda config, **_kwargs: _FakeProvider(),
    )

    # Force both surfaces onto the workspace config file.
    loaded = config_loader.load_config(config_path=workspace / ".lintro-config.yaml")
    monkeypatch.setattr(config_loader, "get_config", lambda: loaded)

    mcp_calls: list[dict[str, Any]] = []

    def _mcp_run_review(context: Any, **kwargs: Any) -> ReviewResult:
        mcp_calls.append({"context": context, **kwargs})
        return _result_with(findings=())

    monkeypatch.setattr(orchestrator, "run_review", _mcp_run_review)

    mcp_review._execute_review(
        arguments={"base": "main"},
        workspace=workspace,
    )
    assert_that(mcp_calls).is_length(1)
    mcp_kwargs = {key: value for key, value in mcp_calls[0].items() if key != "context"}

    cli_calls: list[dict[str, Any]] = []

    def _cli_run_review(context: Any, **kwargs: Any) -> ReviewResult:
        cli_calls.append({"context": context, **kwargs})
        return _result_with(findings=())

    runner = CliRunner()
    with (
        patch("lintro.cli_utils.commands.review.require_ai"),
        patch(
            "lintro.cli_utils.commands.review.get_config",
            return_value=loaded,
        ),
        patch(
            "lintro.cli_utils.commands.review.run_review",
            side_effect=_cli_run_review,
        ),
        patch("lintro.cli_utils.commands.review.render_review_output"),
        patch(
            "lintro.cli_utils.commands.review.get_provider",
            return_value=_FakeProvider(),
        ),
        patch(
            "lintro.cli_utils.commands.review._execute_advisory",
            return_value=[],
        ),
    ):
        cli_result = runner.invoke(cli, ["review", "--base", "main"])

    assert_that(cli_result.exit_code).is_equal_to(0)
    assert_that(cli_calls).is_length(1)
    cli_kwargs = {key: value for key, value in cli_calls[0].items() if key != "context"}

    assert_that(set(mcp_kwargs) & _SHARED_RUN_REVIEW_KWARGS).is_equal_to(
        _SHARED_RUN_REVIEW_KWARGS,
    )
    assert_that(set(cli_kwargs) & _SHARED_RUN_REVIEW_KWARGS).is_equal_to(
        _SHARED_RUN_REVIEW_KWARGS,
    )
    assert_that(set(cli_kwargs) & _CLI_ONLY_RUN_REVIEW_KWARGS).is_equal_to(
        _CLI_ONLY_RUN_REVIEW_KWARGS,
    )
    assert_that(set(mcp_kwargs) & _CLI_ONLY_RUN_REVIEW_KWARGS).is_empty()

    # Shared deterministic prep must agree on depth/checklist text shape and
    # force_semantic_chunking from the same project config.
    assert_that(cli_kwargs["depth"]).is_equal_to(mcp_kwargs["depth"])
    assert_that(cli_kwargs["checklist_text"]).is_equal_to(mcp_kwargs["checklist_text"])
    assert_that(cli_kwargs["force_semantic_chunking"]).is_equal_to(
        mcp_kwargs["force_semantic_chunking"],
    )
    assert_that(cli_kwargs["ai_config"].model).is_equal_to(
        mcp_kwargs["ai_config"].model,
    )
    assert_that(cli_kwargs["ai_config"].provider).is_equal_to(
        mcp_kwargs["ai_config"].provider,
    )


# --- error mapping parity -----------------------------------------------------


def test_cli_json_and_mcp_error_detail_share_the_error_contract_body() -> None:
    """MCP ``review_error`` detail is the same body CLI JSON prints under ``error``."""
    error = AIAuthenticationError(
        "Anthropic authentication failed: Error code: 401 - authentication_error",
    )
    contract = build_error_contract(provider="anthropic", error=error)
    envelope = mcp_review._review_failure(provider_name="anthropic", error=error)

    assert_that(envelope.code).is_equal_to(McpErrorCode.TOOL_UNAVAILABLE)
    assert_that((envelope.detail or {})["review_error"]).is_equal_to(
        contract["error"],
    )
    assert_that(json.loads(json.dumps(contract))["error"]["kind"]).is_equal_to(
        "auth_failed",
    )


def test_mcp_maps_non_unavailable_provider_errors_to_execution_error() -> None:
    """Non-unavailable failures stay EXECUTION_ERROR while keeping the contract body."""
    from lintro.ai.review.errors_taxonomy import ReviewErrorKind
    from lintro.ai.review.exceptions import ReviewExecutionError

    error = ReviewExecutionError(
        message="review failed",
        cause_message="Expecting value",
        error_kind=ReviewErrorKind.INVALID_RESPONSE,
    )
    contract = build_error_contract(provider="anthropic", error=error)
    envelope = mcp_review._review_failure(provider_name="anthropic", error=error)

    assert_that(envelope.code).is_equal_to(McpErrorCode.EXECUTION_ERROR)
    assert_that((envelope.detail or {})["review_error"]).is_equal_to(
        contract["error"],
    )
    assert_that(contract["error"]["kind"]).is_equal_to("invalid_response")


# --- review metadata projection -----------------------------------------------


def test_mcp_review_disabled_maps_to_tool_unavailable(tmp_path: Path) -> None:
    """MCP preparation refuses a workspace with ``ai.review: false`` as unavailable."""
    workspace = tmp_path.resolve()
    (workspace / ".lintro-config.yaml").write_text(
        "ai:\n  enabled: true\n  review: false\n  provider: anthropic\n",
        encoding="utf-8",
    )
    from lintro.config import config_loader

    loaded = config_loader.load_config(config_path=workspace / ".lintro-config.yaml")
    with (
        patch("lintro.ai.availability.is_ai_available", return_value=True),
        patch("lintro.config.config_loader.get_config", return_value=loaded),
        pytest.raises(McpError) as excinfo,
    ):
        mcp_review._resolve_ai_config(workspace=workspace)

    raised = excinfo.value
    assert_that(raised.code).is_equal_to(McpErrorCode.TOOL_UNAVAILABLE)
    detail = raised.envelope.detail or {}
    assert_that(detail["reason"]).is_equal_to("review_disabled")


def test_mcp_invalid_provider_env_maps_to_invalid_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MCP preparation maps a typo'd ``LINTRO_AI_PROVIDER`` to invalid input."""
    monkeypatch.setenv("LINTRO_AI_PROVIDER", "cursur")
    workspace = tmp_path.resolve()
    (workspace / ".lintro-config.yaml").write_text(
        "ai:\n  enabled: true\n  review: true\n  provider: anthropic\n",
        encoding="utf-8",
    )
    from lintro.config import config_loader

    loaded = config_loader.load_config(config_path=workspace / ".lintro-config.yaml")
    with (
        patch("lintro.ai.availability.is_ai_available", return_value=True),
        patch("lintro.config.config_loader.get_config", return_value=loaded),
        pytest.raises(McpError) as excinfo,
    ):
        mcp_review._resolve_ai_config(workspace=workspace)

    raised = excinfo.value
    assert_that(raised.code).is_equal_to(McpErrorCode.INVALID_INPUT)
    assert_that(str(raised)).contains("LINTRO_AI_PROVIDER='cursur'")
    detail = raised.envelope.detail or {}
    assert_that(detail["reason"]).is_equal_to("invalid_ai_override")

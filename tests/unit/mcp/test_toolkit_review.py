"""End-to-end tests for the ``lintro_review`` MCP tool.

Every call goes through a real :class:`mcp.client.Client` over in-memory
streams, so the schema validation, the workspace path guard, and the error
envelope under test are the ones the stdio server actually applies.

The AI provider is always mocked. The orchestrator is stubbed at
``lintro.ai.review.orchestrator.run_review`` (the toolkit imports it lazily,
inside the handler, so patching the module attribute is what the handler sees),
which keeps the tests free of network calls, credentials, and cost while still
exercising context collection, budget resolution, and payload shaping for real.
"""

from __future__ import annotations

import subprocess  # nosec B404 - subprocess runs fixed git argv in a temp repo
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypeVar

import pytest
from assertpy import assert_that
from mcp.client import Client
from mcp.types import CallToolResult, Tool

from lintro.ai.review.models.review_finding import ReviewFinding, Severity
from lintro.ai.review.models.review_metadata import ReviewMetadata
from lintro.ai.review.models.review_result import ReviewResult
from lintro.mcp.enums.mcp_error_code import McpErrorCode
from lintro.mcp.toolkits.review import (
    REVIEW_TIMEOUT_SECONDS,
    STRICTNESS_VALUES,
    build_review_toolkit,
    resolve_budget_policy,
)
from tests.unit.mcp.session_helpers import payload_from_result, run_in_memory_client

_T = TypeVar("_T")

_CONFIG = """ai:
  enabled: true
  review: true
  provider: anthropic
  model: test-model
  max_cost_usd: 1.0
"""

_CONFIG_REVIEW_OFF = """ai:
  enabled: true
  review: false
  provider: anthropic
"""

_CONFIG_NO_PROVIDER = """ai:
  enabled: true
  review: true
"""


def _run_session(
    *,
    workspace: Path,
    check: Callable[[Client], Awaitable[_T]],
) -> _T:
    """Run ``check`` against a connected in-memory MCP client.

    Args:
        workspace: Workspace root for the server under test.
        check: Async callback receiving an initialized client.

    Returns:
        Whatever ``check`` returns.
    """
    return run_in_memory_client(workspace=workspace, check=check)


def _payload(result: CallToolResult) -> dict[str, Any]:
    """Extract a tool result payload as a dict.

    Args:
        result: The ``CallToolResult`` returned by ``client.call_tool``.

    Returns:
        The payload the server sent.
    """
    return payload_from_result(result)


def _call(
    *,
    workspace: Path,
    arguments: dict[str, Any],
) -> tuple[CallToolResult, dict[str, Any]]:
    """Call ``lintro_review`` and return its raw result and decoded payload.

    Args:
        workspace: Workspace root for the server under test.
        arguments: Tool arguments.

    Returns:
        The ``CallToolResult`` and its payload.
    """

    async def _check(
        session: Client,
    ) -> tuple[CallToolResult, dict[str, Any]]:
        result = await session.call_tool(
            name="lintro_review",
            arguments=arguments,
        )
        return result, _payload(result)

    return _run_session(workspace=workspace, check=_check)


def _git(*args: str, cwd: Path) -> None:
    """Run one git command in ``cwd``, failing loudly.

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


class _FakeProvider:
    """Stand-in provider that never issues a call.

    Attributes:
        name: Provider identifier used by the error taxonomy.
        model_name: Model identifier reported in run metadata.
    """

    name = "anthropic"
    model_name = "test-model"


def _metadata(
    *,
    partial: bool = False,
    stopped_reason: str = "",
    chunks_reviewed: int = 1,
) -> ReviewMetadata:
    """Build review metadata for a stubbed run.

    Args:
        partial: Whether the run stopped before every chunk was reviewed.
        stopped_reason: Why it stopped.
        chunks_reviewed: Chunks actually reviewed.

    Returns:
        ReviewMetadata: Metadata for the stubbed result.
    """
    return ReviewMetadata(
        model="test-model",
        provider="anthropic",
        context_window=200000,
        depth=1,
        chunks_total=2,
        chunks_current=chunks_reviewed,
        files_reviewed=1,
        files_total=1,
        checklist_items=3,
        token_usage={"prompt": 10, "completion": 5, "total": 15},
        cost_estimate_usd=0.25,
        base_ref="main",
        head_ref="HEAD",
        timestamp="2026-08-01T00:00:00+00:00",
        partial=partial,
        chunks_reviewed=chunks_reviewed,
        stopped_reason=stopped_reason,
        duration_seconds=12.5,
        phase_timings={
            "context_collection": 0.1,
            "provider": 12.0,
            "parse_merge": 0.4,
        },
    )


def _result(
    *,
    partial: bool = False,
    stopped_reason: str = "",
    chunks_reviewed: int = 1,
) -> ReviewResult:
    """Build a stubbed review result carrying one finding.

    Args:
        partial: Whether the run stopped early.
        stopped_reason: Why it stopped.
        chunks_reviewed: Chunks actually reviewed.

    Returns:
        ReviewResult: The stubbed result.
    """
    finding = ReviewFinding(
        severity=Severity.P1,
        category="correctness",
        file="app.py",
        line=3,
        title="Unbounded loop",
        description="The loop never terminates.",
        cause="The counter is never incremented.",
        fix="Increment the counter.",
        confidence="high",
        checklist_ids=(2,),
        suggested_code="i += 1",
    )
    return ReviewResult(
        metadata=_metadata(
            partial=partial,
            stopped_reason=stopped_reason,
            chunks_reviewed=chunks_reviewed,
        ),
        summary="One blocking issue.",
        findings=(finding,),
    )


def test_finding_dict_carries_suggestion_drop_state() -> None:
    """The MCP finding payload distinguishes dropped from validated patches.

    ``suggested_code`` is the only patch carrier MCP serializes, so a finding
    that survived validation must still show it, and a dropped one must show
    the reason with the patch cleared (#2101).
    """
    from lintro.ai.review.enums.suggestion_drop_reason import SuggestionDropReason
    from lintro.mcp.toolkits.review import _finding_to_dict

    kept = _result().findings[0]
    dropped = ReviewFinding(
        severity=Severity.P2,
        category="correctness",
        file="app.py",
        line=9,
        title="Stale patch",
        description="d",
        cause="c",
        fix="f",
        confidence="high",
        suggestion_dropped=SuggestionDropReason.STALE_ANCHOR,
    )

    kept_payload = _finding_to_dict(finding=kept)
    dropped_payload = _finding_to_dict(finding=dropped)

    assert_that(kept_payload["suggested_code"]).is_equal_to("i += 1")
    assert_that(kept_payload["suggestion_dropped"]).is_equal_to("")
    assert_that(dropped_payload["suggested_code"]).is_equal_to("")
    assert_that(dropped_payload["suggestion_dropped"]).is_equal_to("stale_anchor")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Create a git workspace with one committed change to review.

    Args:
        tmp_path: Pytest-provided temporary directory.

    Returns:
        Path: The resolved workspace root, on a branch ahead of ``main``.
    """
    workspace = tmp_path.resolve()
    (workspace / ".lintro-config.yaml").write_text(_CONFIG, encoding="utf-8")
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


@pytest.fixture
def stub_ai(monkeypatch: pytest.MonkeyPatch) -> Callable[..., list[Any]]:
    """Make AI look available and replace the provider and the orchestrator.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        Callable: Installs a stubbed ``run_review`` and returns the list the
        AI configs it was called with are recorded in.
    """
    import lintro.ai.availability as availability
    import lintro.ai.providers as providers
    import lintro.ai.review.orchestrator as orchestrator

    monkeypatch.setattr(availability, "is_ai_available", lambda: True)
    monkeypatch.setattr(
        providers,
        "get_provider",
        lambda config, **_kwargs: _FakeProvider(),
    )

    def install(
        *,
        result: ReviewResult | None = None,
        error: Exception | None = None,
    ) -> list[Any]:
        calls: list[Any] = []

        def _run_review(context: Any, **kwargs: Any) -> ReviewResult:
            calls.append({"context": context, **kwargs})
            if error is not None:
                raise error
            return result if result is not None else _result()

        monkeypatch.setattr(orchestrator, "run_review", _run_review)
        return calls

    return install


def test_review_is_listed_as_read_only_and_not_idempotent(tmp_path: Path) -> None:
    """The tool advertises the hints its cost and side-effect profile implies."""

    async def _check(session: Client) -> dict[str, Tool]:
        listed = await session.list_tools()
        return {tool.name: tool for tool in listed.tools}

    tools = _run_session(workspace=tmp_path.resolve(), check=_check)

    assert_that(tools).contains_key("lintro_review")
    hints = tools["lintro_review"].annotations
    assert hints is not None
    assert_that(hints.read_only_hint).is_true()
    assert_that(hints.destructive_hint).is_false()
    assert_that(hints.idempotent_hint).is_false()


def test_review_spec_allows_more_time_than_the_default_budget(tmp_path: Path) -> None:
    """A depth-3 review runs for minutes, so the 300s default would abort it."""
    spec = build_review_toolkit(workspace=tmp_path.resolve())[0]

    assert_that(spec.name).is_equal_to("lintro_review")
    assert_that(spec.timeout_seconds).is_equal_to(REVIEW_TIMEOUT_SECONDS)
    assert_that(spec.timeout_seconds).is_greater_than(300.0)
    assert_that(spec.path_arguments).is_equal_to(("paths",))


def test_strictness_choices_match_the_review_enum() -> None:
    """The hand-written schema enum cannot drift from ReviewStrictness."""
    from lintro.ai.review.enums.review_strictness import ReviewStrictness

    assert_that(sorted(STRICTNESS_VALUES)).is_equal_to(
        sorted(level.value for level in ReviewStrictness),
    )


def test_context_error_mapping_uses_real_error_codes() -> None:
    """Every mapped context code is a real ReviewContextErrorCode value."""
    from lintro.ai.review.enums.review_context_error_code import (
        ReviewContextErrorCode,
    )
    from lintro.mcp.toolkits import review as review_toolkit

    known = {code.value for code in ReviewContextErrorCode}
    mapped = (
        review_toolkit._INVALID_INPUT_CONTEXT_CODES
        | review_toolkit._UNAVAILABLE_CONTEXT_CODES
        | {review_toolkit._NO_CHANGES_CODE}
    )

    assert_that(known).contains(*sorted(mapped))


def test_budget_detection_matches_the_engine_stop_reason() -> None:
    """The engine's cost-cap wording is what the budget detector looks for.

    The orchestrator reports a cost-cap stop only in prose, so this pins the
    one string both sides depend on; reword it there and this fails rather than
    the tool silently reporting a capped run as a complete one.
    """
    from lintro.ai.review.orchestrator import _cost_cap_reason
    from lintro.mcp.toolkits.review import _stopped_on_budget

    metadata = _metadata(partial=True, stopped_reason=_cost_cap_reason(cap=1.0))

    assert_that(_stopped_on_budget(metadata=metadata)).is_true()


def test_review_rejects_a_base_combined_with_uncommitted(
    repo: Path,
    stub_ai: Callable[..., list[Any]],
) -> None:
    """Two mutually exclusive diff modes are an input error, not a crash."""
    stub_ai()

    result, payload = _call(
        workspace=repo,
        arguments={"base": "main", "uncommitted": True},
    )

    assert_that(result.is_error).is_true()
    assert_that(payload["error"]["code"]).is_equal_to(McpErrorCode.INVALID_INPUT.value)
    assert_that(payload["error"]["detail"]["context_error"]).is_equal_to(
        "invalid-review-mode",
    )


@pytest.mark.parametrize(
    ("requested", "configured", "effective", "clamped"),
    [
        (None, 1.0, 1.0, False),
        (0.5, None, 0.5, False),
        (0.5, 1.0, 0.5, False),
        (2.0, 1.0, 1.0, True),
        (None, None, None, False),
    ],
)
def test_budget_policy_never_raises_the_configured_ceiling(
    requested: float | None,
    configured: float | None,
    effective: float | None,
    clamped: bool,
) -> None:
    """An argument can only lower the operator's ai.max_cost_usd ceiling."""
    policy = resolve_budget_policy(requested=requested, configured=configured)

    assert_that(policy.effective_usd).is_equal_to(effective)
    assert_that(policy.clamped).is_equal_to(clamped)


def test_review_returns_findings_and_run_metadata(
    repo: Path,
    stub_ai: Callable[..., list[Any]],
) -> None:
    """A completed review is shaped as findings plus run and budget metadata."""
    stub_ai()

    result, payload = _call(workspace=repo, arguments={"base": "main"})

    assert_that(result.is_error).is_false()
    assert_that(payload["summary"]).is_equal_to("One blocking issue.")
    finding = payload["findings"][0]
    assert_that(finding).contains_key(
        "file",
        "line",
        "severity",
        "category",
        "title",
        "body",
        "confidence",
    )
    assert_that(finding["file"]).is_equal_to("app.py")
    assert_that(finding["severity"]).is_equal_to("P1")
    assert_that(finding["body"]).contains("The loop never terminates.")
    assert_that(finding["body"]).contains("Cause:")
    assert_that(finding["body"]).contains("Fix: Increment the counter.")

    run = payload["run"]
    assert_that(run["model"]).is_equal_to("test-model")
    assert_that(run["cost_usd"]).is_equal_to(0.25)
    assert_that(run["duration_seconds"]).is_equal_to(12.5)
    assert_that(run["phase_timings"]).is_equal_to(
        {
            "context_collection": 0.1,
            "provider": 12.0,
            "parse_merge": 0.4,
        },
    )
    assert_that(run["chunks"]).is_equal_to({"total": 2, "reviewed": 1})
    assert_that(run["partial"]).is_false()
    assert_that(payload["budget"]["exceeded"]).is_false()


def test_review_passes_depth_and_strictness_through(
    repo: Path,
    stub_ai: Callable[..., list[Any]],
) -> None:
    """Requested depth and strictness reach the orchestrator unchanged."""
    calls = stub_ai()

    result, _payload_body = _call(
        workspace=repo,
        arguments={"base": "main", "depth": 3, "strictness": "focused"},
    )

    assert_that(result.is_error).is_false()
    assert_that(calls[0]["depth"]).is_equal_to(3)
    assert_that(calls[0]["sensitivity"].strictness.value).is_equal_to("focused")


def test_review_applies_the_resolved_transport_profile(
    repo: Path,
    stub_ai: Callable[..., list[Any]],
) -> None:
    """A CLI transport profile's timeout reaches the orchestrator config.

    ``_execute_review`` is the only MCP path that resolves transport
    profiles; if ``apply_resolved_transport`` is unwired, the orchestrator
    would run a CLI review on the API-sized 60s default (#1923).
    """
    (repo / ".lintro-config.yaml").write_text(
        _CONFIG + ("  transport: cli\n  transports:\n    cli:\n      timeout: 555.0\n"),
        encoding="utf-8",
    )
    calls = stub_ai()

    result, _payload_body = _call(workspace=repo, arguments={"base": "main"})

    assert_that(result.is_error).is_false()
    assert_that(calls[0]["ai_config"].api_timeout).is_equal_to(555.0)


def test_review_clamps_the_requested_budget_to_the_configured_ceiling(
    repo: Path,
    stub_ai: Callable[..., list[Any]],
) -> None:
    """An agent asking for more money than the config allows gets the config."""
    calls = stub_ai()

    result, payload = _call(
        workspace=repo,
        arguments={"base": "main", "max_cost_usd": 50.0},
    )

    assert_that(result.is_error).is_false()
    assert_that(calls[0]["ai_config"].max_cost_usd).is_equal_to(1.0)
    assert_that(payload["budget"]["requested_usd"]).is_equal_to(50.0)
    assert_that(payload["budget"]["configured_usd"]).is_equal_to(1.0)
    assert_that(payload["budget"]["effective_usd"]).is_equal_to(1.0)
    assert_that(payload["budget"]["clamped"]).is_true()


def test_review_honors_a_lower_requested_budget(
    repo: Path,
    stub_ai: Callable[..., list[Any]],
) -> None:
    """A tighter per-call ceiling is handed to the review engine."""
    calls = stub_ai()

    result, payload = _call(
        workspace=repo,
        arguments={"base": "main", "max_cost_usd": 0.05},
    )

    assert_that(result.is_error).is_false()
    assert_that(calls[0]["ai_config"].max_cost_usd).is_equal_to(0.05)
    assert_that(payload["budget"]["clamped"]).is_false()


def test_review_reports_a_partial_run_without_discarding_findings(
    repo: Path,
    stub_ai: Callable[..., list[Any]],
) -> None:
    """A cap reached mid-run still returns what was reviewed, marked partial."""
    stub_ai(
        result=_result(
            partial=True,
            stopped_reason="cost cap ($1.00) reached",
            chunks_reviewed=1,
        ),
    )

    result, payload = _call(workspace=repo, arguments={"base": "main"})

    assert_that(result.is_error).is_false()
    assert_that(payload["findings"]).is_length(1)
    assert_that(payload["run"]["partial"]).is_true()
    assert_that(payload["run"]["stopped_reason"]).contains("cost cap")
    assert_that(payload["budget"]["exceeded"]).is_true()


def test_review_reports_budget_exceeded_when_nothing_was_reviewed(
    repo: Path,
    stub_ai: Callable[..., list[Any]],
) -> None:
    """A cap that stops the run before any chunk is a structured failure."""
    stub_ai(
        result=ReviewResult(
            metadata=_metadata(
                partial=True,
                stopped_reason="cost cap ($0.01) reached",
                chunks_reviewed=0,
            ),
            summary="",
        ),
    )

    result, payload = _call(
        workspace=repo,
        arguments={"base": "main", "max_cost_usd": 0.01},
    )

    assert_that(result.is_error).is_true()
    assert_that(payload["error"]["code"]).is_equal_to(
        McpErrorCode.BUDGET_EXCEEDED.value,
    )
    assert_that(payload["error"]["detail"]["budget"]["exceeded"]).is_true()
    assert_that(payload["error"]["detail"]["budget"]["effective_usd"]).is_equal_to(0.01)


def test_review_reports_an_empty_diff_as_a_result_not_a_failure(
    repo: Path,
    stub_ai: Callable[..., list[Any]],
) -> None:
    """Nothing to review is an answer with the same keys as a real review."""
    calls = stub_ai()

    result, payload = _call(workspace=repo, arguments={"base": "feature"})

    assert_that(result.is_error).is_false()
    assert_that(payload["findings"]).is_empty()
    assert_that(payload["run"]).contains_key("model", "cost_usd", "chunks")
    assert_that(payload["run"]["cost_usd"]).is_equal_to(0.0)
    assert_that(calls).is_empty()


def test_review_surfaces_a_provider_failure_with_its_taxonomy(
    repo: Path,
    stub_ai: Callable[..., list[Any]],
) -> None:
    """A failed review carries the review error contract as error detail."""
    from lintro.ai.exceptions import AIError

    stub_ai(error=AIError("401 invalid x-api-key"))

    result, payload = _call(workspace=repo, arguments={"base": "main"})

    assert_that(result.is_error).is_true()
    assert_that(payload["error"]["code"]).is_in(
        McpErrorCode.EXECUTION_ERROR.value,
        McpErrorCode.TOOL_UNAVAILABLE.value,
    )
    assert_that(payload["error"]["detail"]["review_error"]).contains_key(
        "kind",
        "provider",
        "retryable",
    )


def test_review_maps_a_too_large_diff_to_invalid_input(
    repo: Path,
    stub_ai: Callable[..., list[Any]],
) -> None:
    """A diff-size refusal raised inside ``run_review`` is invalid input.

    The CLI byte ceiling (#1967) raises ``ReviewContextError`` after context
    collection; without the dedicated handler it would be misreported as a
    provider/execution failure instead of a size refusal the caller can fix
    with ``paths`` or the api transport.
    """
    from lintro.ai.review.enums.review_context_error_code import (
        ReviewContextErrorCode,
    )
    from lintro.ai.review.exceptions import ReviewContextError

    stub_ai(
        error=ReviewContextError(
            "diff exceeds ai.cli_max_diff_bytes",
            code=ReviewContextErrorCode.DIFF_TOO_LARGE,
        ),
    )

    result, payload = _call(workspace=repo, arguments={"base": "main"})

    assert_that(result.is_error).is_true()
    assert_that(payload["error"]["code"]).is_equal_to(
        McpErrorCode.INVALID_INPUT.value,
    )
    assert_that(payload["error"]["detail"]["context_error"]).is_equal_to(
        "diff-too-large",
    )


def test_review_is_unavailable_without_the_ai_extra(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The tool stays listed and explains itself instead of vanishing."""
    import lintro.ai.availability as availability

    monkeypatch.setattr(availability, "is_ai_available", lambda: False)

    result, payload = _call(workspace=repo, arguments={"base": "main"})

    assert_that(result.is_error).is_true()
    assert_that(payload["error"]["code"]).is_equal_to(
        McpErrorCode.TOOL_UNAVAILABLE.value,
    )
    assert_that(payload["error"]["detail"]["reason"]).is_equal_to("ai_unavailable")


def test_review_is_unavailable_when_provider_is_unset(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An enabled review without ``ai.provider`` is unavailable, even on empty diffs."""
    import lintro.ai.availability as availability

    monkeypatch.setattr(availability, "is_ai_available", lambda: True)
    (repo / ".lintro-config.yaml").write_text(_CONFIG_NO_PROVIDER, encoding="utf-8")

    empty_result, empty_payload = _call(workspace=repo, arguments={"base": "feature"})
    changed_result, changed_payload = _call(workspace=repo, arguments={"base": "main"})

    for result, payload in (
        (empty_result, empty_payload),
        (changed_result, changed_payload),
    ):
        assert_that(result.is_error).is_true()
        assert_that(payload["error"]["code"]).is_equal_to(
            McpErrorCode.TOOL_UNAVAILABLE.value,
        )
        assert_that(payload["error"]["detail"]["reason"]).is_equal_to(
            "provider_unavailable",
        )
        assert_that(payload["error"]["message"]).contains("ai.provider")


def test_review_is_unavailable_when_the_workspace_disables_it(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``ai.review: false`` is reported as unavailability, not a crash."""
    import lintro.ai.availability as availability

    monkeypatch.setattr(availability, "is_ai_available", lambda: True)
    (repo / ".lintro-config.yaml").write_text(_CONFIG_REVIEW_OFF, encoding="utf-8")

    result, payload = _call(workspace=repo, arguments={"base": "main"})

    assert_that(result.is_error).is_true()
    assert_that(payload["error"]["code"]).is_equal_to(
        McpErrorCode.TOOL_UNAVAILABLE.value,
    )
    assert_that(payload["error"]["detail"]["reason"]).is_equal_to("review_disabled")
    assert_that(payload["error"]["message"]).contains("LINTRO_AI_ENABLED=1")
    assert_that(payload["error"]["message"]).contains("LINTRO_AI_REVIEW=1")


def test_review_rejects_a_depth_outside_the_supported_range(repo: Path) -> None:
    """Schema validation refuses depth 4 before any provider call is made."""
    result, payload = _call(workspace=repo, arguments={"base": "main", "depth": 4})

    assert_that(result.is_error).is_true()
    assert_that(payload["error"]["code"]).is_equal_to(McpErrorCode.INVALID_INPUT.value)


def test_review_rejects_an_unknown_argument(repo: Path) -> None:
    """``--post`` has no MCP equivalent, and no argument is silently ignored."""
    result, payload = _call(workspace=repo, arguments={"post": True})

    assert_that(result.is_error).is_true()
    assert_that(payload["error"]["code"]).is_equal_to(McpErrorCode.INVALID_INPUT.value)


def test_review_rejects_a_path_outside_the_workspace(repo: Path) -> None:
    """The server's path guard covers the review tool's paths argument."""
    result, payload = _call(
        workspace=repo,
        arguments={"base": "main", "paths": ["../secrets"]},
    )

    assert_that(result.is_error).is_true()
    assert_that(payload["error"]["code"]).is_equal_to(
        McpErrorCode.WORKSPACE_VIOLATION.value,
    )


def test_review_filters_the_diff_to_the_requested_paths(
    repo: Path,
    stub_ai: Callable[..., list[Any]],
) -> None:
    """Absolute path arguments are mapped back to repo-relative prefixes."""
    calls = stub_ai()

    result, _payload_body = _call(
        workspace=repo,
        arguments={"base": "main", "paths": ["app.py"]},
    )

    assert_that(result.is_error).is_false()
    assert_that(calls).is_length(1)
    reviewed = [file.path for file in calls[0]["context"].changed_files]
    assert_that(reviewed).is_equal_to(["app.py"])


def test_review_includes_a_lint_digest_when_asked(
    repo: Path,
    stub_ai: Callable[..., list[Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``with_lint`` feeds the deterministic linters' digest into the prompt."""
    import lintro.ai.review.lint_bridge as lint_bridge

    monkeypatch.setattr(
        lint_bridge,
        "run_lint_on_changed_files",
        lambda **_kwargs: [],
    )
    monkeypatch.setattr(
        lint_bridge,
        "format_lint_results_for_prompt",
        lambda **_kwargs: "ruff: 1 issue",
    )
    calls = stub_ai()

    result, _payload_body = _call(
        workspace=repo,
        arguments={"base": "main", "with_lint": True},
    )

    assert_that(result.is_error).is_false()
    assert_that(calls[0]["lint_results"]).is_equal_to("ruff: 1 issue")


def test_review_omits_the_lint_digest_by_default(
    repo: Path,
    stub_ai: Callable[..., list[Any]],
) -> None:
    """Without ``with_lint`` no linters run and no digest is sent."""
    calls = stub_ai()

    result, _payload_body = _call(workspace=repo, arguments={"base": "main"})

    assert_that(result.is_error).is_false()
    assert_that(calls[0]["lint_results"]).is_none()


def test_review_reports_no_changes_for_a_path_matching_nothing(
    repo: Path,
    stub_ai: Callable[..., list[Any]],
) -> None:
    """A path filter that excludes the whole diff yields an empty result."""
    (repo / "other.py").write_text("z = 3\n", encoding="utf-8")
    calls = stub_ai()

    result, payload = _call(
        workspace=repo,
        arguments={"base": "main", "paths": ["other.py"]},
    )

    assert_that(result.is_error).is_false()
    assert_that(payload["findings"]).is_empty()
    # #2003: an empty run is trivially complete and carries the same key.
    assert_that(payload["coverage_complete"]).is_true()
    assert_that(calls).is_empty()

"""Tests verifying config knobs flow through to the functions that use them.

Phase 3.4: After context_lines, fix_search_radius, retry delays, and
timeout were wired (Phase 2.1-2.5), these tests confirm the values
actually arrive at the downstream functions.

The downstream collaborators are replaced with plain recording stand-ins
rather than mocks, so each test asserts on a list the fake really appended to
plus the value the pipeline returned (#2315).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from assertpy import assert_that

from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport
from lintro.ai.models import AIFixSuggestion
from lintro.ai.pipeline import run_fix_pipeline
from lintro.ai.validation import ValidationResult
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.base_issue import BaseIssue
from tests.unit.ai.conftest import MockAIProvider, MockIssue, RecordingConsoleLogger

if TYPE_CHECKING:
    from collections.abc import Sequence

_PIPELINE = "lintro.ai.pipeline"


def _make_suggestion(
    *,
    tool_name: str = "ruff",
    code: str = "E501",
) -> AIFixSuggestion:
    """Build one fix suggestion for the pipeline to carry.

    Args:
        tool_name: Tool the suggestion is attributed to.
        code: Rule code the suggestion addresses.

    Returns:
        The suggestion.
    """
    suggestion = AIFixSuggestion(file="a.py", line=1, code=code)
    suggestion.tool_name = tool_name
    return suggestion


def _make_fix_issues() -> tuple[
    list[tuple[ToolResult, BaseIssue]],
    ToolResult,
    MockIssue,
]:
    """Build the single-issue input the pipeline tests run over.

    Returns:
        The ``(fix_issues, result, issue)`` triple.
    """
    issue = MockIssue(file="a.py", line=1, code="E501", message="err")
    result = ToolResult(
        name="ruff",
        success=False,
        issues_count=1,
        issues=[issue],
    )
    return [(result, issue)], result, issue


@dataclass
class _PipelineRecorder:
    """Records what the fix pipeline hands to each downstream collaborator.

    Attributes:
        fix_params: One entry per ``generate_fixes_from_params`` call, holding
            the params object the pipeline built from the config.
        apply_calls: Keyword arguments of every ``apply_fixes`` call.
        post_summary_calls: Keyword arguments of every
            ``generate_post_fix_summary`` call.
    """

    fix_params: list[Any] = field(default_factory=list)
    apply_calls: list[dict[str, Any]] = field(default_factory=list)
    post_summary_calls: list[dict[str, Any]] = field(default_factory=list)


def _install_pipeline_recorder(
    *,
    monkeypatch: pytest.MonkeyPatch,
    suggestions: Sequence[AIFixSuggestion] = (),
    validation: ValidationResult | None = None,
) -> _PipelineRecorder:
    """Replace the pipeline's collaborators with recording stand-ins.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        suggestions: Suggestions the fake generator returns, and which the
            fake applier reports as applied.
        validation: Validation result the fake verifier returns.

    Returns:
        The recorder the installed fakes append to.
    """
    recorder = _PipelineRecorder()
    applied = list(suggestions)

    async def _generate_fixes(
        _issues: object,
        _provider: object,
        params: object,
    ) -> list[AIFixSuggestion]:
        recorder.fix_params.append(params)
        return list(applied)

    def _apply_fixes(
        candidates: Sequence[AIFixSuggestion],
        **kwargs: Any,
    ) -> list[AIFixSuggestion]:
        recorder.apply_calls.append(kwargs)
        return list(candidates)

    def _review_interactive(
        *_args: Any,
        **_kwargs: Any,
    ) -> tuple[int, int, list[AIFixSuggestion]]:
        return 0, 0, []

    async def _post_fix_summary(**kwargs: Any) -> None:
        recorder.post_summary_calls.append(kwargs)
        return None

    def _verify_fixes(**_kwargs: Any) -> ValidationResult | None:
        return validation

    monkeypatch.setattr(f"{_PIPELINE}.generate_fixes_from_params", _generate_fixes)
    monkeypatch.setattr(f"{_PIPELINE}.apply_fixes", _apply_fixes)
    monkeypatch.setattr(f"{_PIPELINE}.review_fixes_interactive", _review_interactive)
    monkeypatch.setattr(f"{_PIPELINE}.generate_post_fix_summary", _post_fix_summary)
    monkeypatch.setattr(f"{_PIPELINE}.verify_fixes", _verify_fixes)
    monkeypatch.setattr(f"{_PIPELINE}.render_validation", lambda *_a, **_k: "")
    monkeypatch.setattr(f"{_PIPELINE}.render_summary", lambda *_a, **_k: "")
    return recorder


# -- context_lines wiring ---------------------------------------------------


async def test_context_lines_flows_to_generate_fixes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``ai_config.context_lines`` reaches the fix-generation params.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    fix_issues, _result, _issue = _make_fix_issues()
    recorder = _install_pipeline_recorder(monkeypatch=monkeypatch)

    ai_config = AIConfig(enabled=True, transport=AITransport.API, context_lines=42)

    await run_fix_pipeline(
        fix_issues=fix_issues,
        provider=MockAIProvider(),
        ai_config=ai_config,
        logger=RecordingConsoleLogger(),
        output_format="json",
        workspace_root=Path("/tmp"),
    )

    assert_that(recorder.fix_params).is_length(1)
    assert_that(recorder.fix_params[0].context_lines).is_equal_to(42)


# -- fix_search_radius wiring -----------------------------------------------


async def test_fix_search_radius_flows_to_apply_fixes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``ai_config.fix_search_radius`` reaches every ``apply_fixes`` call.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    fix_issues, _result, _issue = _make_fix_issues()
    recorder = _install_pipeline_recorder(
        monkeypatch=monkeypatch,
        suggestions=[_make_suggestion()],
        validation=ValidationResult(),
    )

    ai_config = AIConfig(
        enabled=True,
        transport=AITransport.API,
        auto_apply=True,
        fix_search_radius=25,
    )

    await run_fix_pipeline(
        fix_issues=fix_issues,
        provider=MockAIProvider(),
        ai_config=ai_config,
        logger=RecordingConsoleLogger(),
        output_format="json",
        workspace_root=Path("/tmp"),
    )

    assert_that(recorder.apply_calls).is_not_empty()
    radii = [call["search_radius"] for call in recorder.apply_calls]
    assert_that(set(radii)).is_equal_to({25})


# -- retry delay wiring -----------------------------------------------------


async def test_retry_delays_flow_to_generate_fixes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retry delay config values reach the fix-generation params.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    fix_issues, _result, _issue = _make_fix_issues()
    recorder = _install_pipeline_recorder(monkeypatch=monkeypatch)

    ai_config = AIConfig(
        enabled=True,
        transport=AITransport.API,
        retry_base_delay=0.5,
        retry_max_delay=10.0,
        retry_backoff_factor=3.0,
    )

    await run_fix_pipeline(
        fix_issues=fix_issues,
        provider=MockAIProvider(),
        ai_config=ai_config,
        logger=RecordingConsoleLogger(),
        output_format="json",
        workspace_root=Path("/tmp"),
    )

    assert_that(recorder.fix_params).is_length(1)
    params = recorder.fix_params[0]
    assert_that(params.base_delay).is_equal_to(0.5)
    assert_that(params.max_delay).is_equal_to(10.0)
    assert_that(params.backoff_factor).is_equal_to(3.0)


# -- timeout wiring to post-fix summary ------------------------------------


async def test_timeout_and_retries_flow_to_post_fix_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``api_timeout`` and retry config reach ``generate_post_fix_summary``.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    fix_issues, _result, _issue = _make_fix_issues()
    recorder = _install_pipeline_recorder(
        monkeypatch=monkeypatch,
        suggestions=[_make_suggestion()],
        validation=ValidationResult(
            verified=1,
            unverified=0,
            verified_by_tool={"ruff": 1},
            unverified_by_tool={"ruff": 0},
        ),
    )

    ai_config = AIConfig(
        enabled=True,
        transport=AITransport.API,
        auto_apply=True,
        api_timeout=120.0,
        max_retries=5,
        retry_base_delay=2.0,
        retry_max_delay=60.0,
        retry_backoff_factor=4.0,
    )

    await run_fix_pipeline(
        fix_issues=fix_issues,
        provider=MockAIProvider(),
        ai_config=ai_config,
        logger=RecordingConsoleLogger(),
        output_format="terminal",
        workspace_root=Path("/tmp"),
    )

    assert_that(recorder.post_summary_calls).is_length(1)
    kwargs = recorder.post_summary_calls[0]
    assert_that(kwargs["timeout"]).is_equal_to(120.0)
    assert_that(kwargs["max_retries"]).is_equal_to(5)
    assert_that(kwargs["base_delay"]).is_equal_to(2.0)
    assert_that(kwargs["max_delay"]).is_equal_to(60.0)
    assert_that(kwargs["backoff_factor"]).is_equal_to(4.0)


# -- timeout wiring to summary in orchestrator -----------------------------


def test_timeout_and_retries_flow_to_generate_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``api_timeout`` and retry config reach ``generate_summary``.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    from lintro.ai.orchestrator import run_ai_enhancement
    from lintro.config.lintro_config import LintroConfig
    from lintro.enums.action import Action

    summary_calls: list[dict[str, Any]] = []

    async def _generate_summary(*_args: Any, **kwargs: Any) -> None:
        summary_calls.append(kwargs)
        return None

    monkeypatch.setattr(
        "lintro.ai.orchestrator.generate_summary",
        _generate_summary,
    )
    monkeypatch.setattr(
        "lintro.ai.orchestrator.require_ai",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "lintro.ai.orchestrator.get_provider",
        lambda *_a, **_k: MockAIProvider(),
    )

    async def _run_fix_pipeline(**_kwargs: Any) -> Any:
        return None

    monkeypatch.setattr(
        "lintro.ai.orchestrator.run_fix_pipeline",
        _run_fix_pipeline,
    )

    config = LintroConfig(
        ai=AIConfig(
            enabled=True,
            transport=AITransport.API,
            api_timeout=90.0,
            max_retries=4,
            retry_base_delay=1.5,
            retry_max_delay=20.0,
            retry_backoff_factor=2.5,
        ).model_dump(),
    )

    result = ToolResult(
        name="ruff",
        success=False,
        issues_count=1,
        issues=[MockIssue(file="x.py", line=1, message="err", code="E501")],
    )

    run_ai_enhancement(
        action=Action.CHECK,
        all_results=[result],
        lintro_config=config,
        logger=RecordingConsoleLogger(),
        output_format="terminal",
    )

    assert_that(summary_calls).is_length(1)
    kwargs = summary_calls[0]
    assert_that(kwargs["timeout"]).is_equal_to(90.0)
    assert_that(kwargs["max_retries"]).is_equal_to(4)
    assert_that(kwargs["base_delay"]).is_equal_to(1.5)
    assert_that(kwargs["max_delay"]).is_equal_to(20.0)
    assert_that(kwargs["backoff_factor"]).is_equal_to(2.5)

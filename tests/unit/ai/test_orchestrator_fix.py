"""Tests for AI orchestrator fix action."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport
from lintro.ai.models import AIFixSuggestion
from lintro.ai.orchestrator import run_ai_enhancement
from lintro.ai.validation import ValidationResult
from lintro.config.lintro_config import LintroConfig
from lintro.enums.action import Action
from lintro.models.core.tool_result import ToolResult
from tests.unit.ai.conftest import (
    MockAIProvider,
    MockIssue,
    RecordingConsoleLogger,
)


@dataclass
class _OrchestratorRecorder:
    """Records what the orchestrator's fix pipeline hands to each stage.

    Plain lists rather than mock bookkeeping, so the assertions read data the
    stand-ins really captured (#2315).

    Attributes:
        generate_issues: Issue list of each ``generate_fixes_from_params`` call.
        generate_params: Params object of each such call.
        apply_batches: Suggestion batch of each ``apply_fixes`` call.
        verify_kwargs: Keyword arguments of each ``verify_fixes`` call.
        post_summary_kwargs: Keyword arguments of each
            ``generate_post_fix_summary`` call.
    """

    generate_issues: list[list[Any]] = field(default_factory=list)
    generate_params: list[Any] = field(default_factory=list)
    apply_batches: list[list[AIFixSuggestion]] = field(default_factory=list)
    verify_kwargs: list[dict[str, Any]] = field(default_factory=list)
    post_summary_kwargs: list[dict[str, Any]] = field(default_factory=list)


def _install_orchestrator_fakes(
    *,
    monkeypatch: pytest.MonkeyPatch,
    suggestions: Sequence[AIFixSuggestion] = (),
    applied: Sequence[AIFixSuggestion] | None = None,
    validation: ValidationResult | None = None,
) -> _OrchestratorRecorder:
    """Wire the orchestrator to recording stand-ins instead of real AI calls.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        suggestions: Suggestions the fake generator returns.
        applied: Suggestions the fake applier reports as applied; ``None``
            means "whatever batch it was given".
        validation: Result the fake verifier returns.

    Returns:
        The recorder the installed stand-ins append to.
    """
    recorder = _OrchestratorRecorder()

    async def _generate_fixes(
        issues: Sequence[Any],
        _provider: object,
        params: object,
    ) -> list[AIFixSuggestion]:
        recorder.generate_issues.append(list(issues))
        recorder.generate_params.append(params)
        return list(suggestions)

    def _apply_fixes(
        candidates: Sequence[AIFixSuggestion],
        **_kwargs: Any,
    ) -> list[AIFixSuggestion]:
        recorder.apply_batches.append(list(candidates))
        return list(candidates) if applied is None else list(applied)

    def _verify_fixes(**kwargs: Any) -> ValidationResult | None:
        recorder.verify_kwargs.append(kwargs)
        return validation

    async def _post_fix_summary(**kwargs: Any) -> None:
        recorder.post_summary_kwargs.append(kwargs)
        return None

    monkeypatch.setattr("lintro.ai.orchestrator.require_ai", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "lintro.ai.orchestrator.get_provider",
        lambda *_a, **_k: MockAIProvider(),
    )
    monkeypatch.setattr(
        "lintro.ai.orchestrator._resolve_issue_path",
        lambda *, file, workspace_root, cwd: Path(file),
    )
    monkeypatch.setattr(
        "lintro.ai.pipeline.generate_fixes_from_params",
        _generate_fixes,
    )
    monkeypatch.setattr("lintro.ai.pipeline.apply_fixes", _apply_fixes)
    monkeypatch.setattr("lintro.ai.pipeline.verify_fixes", _verify_fixes)
    monkeypatch.setattr(
        "lintro.ai.pipeline.generate_post_fix_summary",
        _post_fix_summary,
    )
    return recorder


# ---------------------------------------------------------------------------
# Fix action tests
# ---------------------------------------------------------------------------


@patch("lintro.ai.orchestrator.require_ai")
@patch("lintro.ai.orchestrator.get_provider")
@patch("lintro.ai.pipeline.generate_fixes_from_params")
@patch("lintro.ai.pipeline.verify_fixes")
@patch("lintro.ai.pipeline.apply_fixes")
@patch(
    "lintro.ai.orchestrator._resolve_issue_path",
    side_effect=lambda *, file, workspace_root, cwd: Path(file),
)
def test_run_ai_enhancement_fix_action_generates_fix_metadata(
    _mock_normalize,
    mock_apply_fixes,
    mock_verify_fixes,
    mock_generate_fixes,
    mock_get_provider,
    _mock_require_ai,
):
    """Verify fix action populates applied/verified counts."""
    result = ToolResult(
        name="ruff",
        success=False,
        issues_count=1,
        issues=[
            MockIssue(
                file="src/main.py",
                line=1,
                message="Use of assert",
                code="B101",
            ),
        ],
    )
    config = LintroConfig(
        ai=AIConfig(
            enabled=True,
            transport=AITransport.API,
            max_fix_attempts=5,
            auto_apply=True,
        ).model_dump(),
    )
    logger = MagicMock()

    mock_get_provider.return_value = MockAIProvider()
    suggestion = AIFixSuggestion(
        file="src/main.py",
        line=1,
        code="B101",
        explanation="Replace assert",
    )
    suggestion.tool_name = "ruff"
    mock_generate_fixes.return_value = [suggestion]
    mock_apply_fixes.return_value = [suggestion]
    mock_verify_fixes.return_value = ValidationResult(
        verified=1,
        unverified=0,
        verified_by_tool={"ruff": 1},
        unverified_by_tool={"ruff": 0},
    )

    run_ai_enhancement(
        action=Action.FIX,
        all_results=[result],
        lintro_config=config,
        logger=logger,
        output_format="terminal",
    )

    assert_that(result.metadata).is_not_none()
    assert_that(result.metadata).contains_key("fix_suggestions")
    assert_that(result.metadata).contains_key("applied_count")
    assert_that(result.metadata).contains_key("verified_count")
    assert_that(result.metadata).contains_key("unverified_count")
    assert_that(result.metadata["fix_suggestions"]).is_length(1)  # type: ignore[index]  # assertpy is_not_none narrows this
    assert_that(result.metadata["applied_count"]).is_equal_to(1)  # type: ignore[index]  # assertpy is_not_none narrows this
    assert_that(result.metadata["verified_count"]).is_equal_to(1)  # type: ignore[index]  # assertpy is_not_none narrows this
    assert_that(result.metadata["unverified_count"]).is_equal_to(0)  # type: ignore[index]  # assertpy is_not_none narrows this


def test_run_ai_enhancement_fix_action_passes_validate_mode_to_interactive_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ``validate_after_group`` flag reaches the interactive review stage.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    result = ToolResult(
        name="ruff",
        success=False,
        issues_count=1,
        issues=[
            MockIssue(
                file="src/main.py",
                line=1,
                message="Use of assert",
                code="B101",
            ),
        ],
    )
    config = LintroConfig(
        ai=AIConfig(
            enabled=True,
            transport=AITransport.API,
            max_fix_attempts=5,
            validate_after_group=True,
        ).model_dump(),
    )

    suggestion = AIFixSuggestion(
        file="src/main.py",
        line=1,
        code="B101",
        explanation="Replace assert",
    )
    review_kwargs: list[dict[str, Any]] = []

    def _review_interactive(
        *_args: Any,
        **kwargs: Any,
    ) -> tuple[int, int, list[AIFixSuggestion]]:
        review_kwargs.append(kwargs)
        return 0, 0, []

    _install_orchestrator_fakes(monkeypatch=monkeypatch, suggestions=[suggestion])
    monkeypatch.setattr(
        "lintro.ai.pipeline.review_fixes_interactive",
        _review_interactive,
    )
    monkeypatch.setattr("lintro.ai.pipeline.sys.stdin.isatty", lambda: True)

    run_ai_enhancement(
        action=Action.FIX,
        all_results=[result],
        lintro_config=config,
        logger=RecordingConsoleLogger(),
        output_format="terminal",
    )

    assert_that(review_kwargs).is_length(1)
    assert_that(review_kwargs[0]["validate_after_group"]).is_true()


def test_run_ai_enhancement_fix_action_uses_only_remaining_issue_tail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fix generation sees only the still-failing issues, not the fixed ones.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    fixed_issue = MockIssue(
        file="src/main.py",
        line=1,
        message="Already fixed",
        code="FORMAT",
    )
    remaining_issue = MockIssue(
        file="src/main.py",
        line=2,
        message="Still failing",
        code="E501",
    )
    result = ToolResult(
        name="prettier",
        success=False,
        issues_count=1,
        issues=[fixed_issue, remaining_issue],
        remaining_issues_count=1,
    )
    config = LintroConfig(
        ai=AIConfig(
            enabled=True,
            transport=AITransport.API,
            max_fix_attempts=5,
        ).model_dump(),
    )

    recorder = _install_orchestrator_fakes(monkeypatch=monkeypatch)

    run_ai_enhancement(
        action=Action.FIX,
        all_results=[result],
        lintro_config=config,
        logger=RecordingConsoleLogger(),
        output_format="json",
    )

    assert_that(recorder.generate_issues).is_length(1)
    codes = [issue.code for issue in recorder.generate_issues[0]]
    assert_that(codes).is_equal_to(["E501"])
    params = recorder.generate_params[0]
    assert_that(params.max_tokens).is_equal_to(4096)
    assert_that(params.workspace_root).is_not_none()


@patch("lintro.ai.orchestrator.require_ai")
@patch("lintro.ai.orchestrator.get_provider")
@patch("lintro.ai.pipeline.generate_fixes_from_params")
def test_run_ai_enhancement_fix_action_skips_tools_with_zero_remaining_issues(
    mock_generate_fixes,
    mock_get_provider,
    _mock_require_ai,
):
    """Verify fix generation is skipped for tools with zero remaining issues."""
    result = ToolResult(
        name="prettier",
        success=True,
        issues_count=0,
        issues=[
            MockIssue(
                file="src/main.py",
                line=1,
                message="Initial issue",
                code="FORMAT",
            ),
        ],
        remaining_issues_count=0,
    )
    config = LintroConfig(
        ai=AIConfig(
            enabled=True,
            transport=AITransport.API,
            max_fix_attempts=5,
        ).model_dump(),
    )
    logger = MagicMock()

    mock_get_provider.return_value = MockAIProvider()

    generated: list[object] = []
    mock_generate_fixes.side_effect = lambda *args, **kwargs: generated.append(args)

    ai_result = run_ai_enhancement(
        action=Action.FIX,
        all_results=[result],
        lintro_config=config,
        logger=logger,
        output_format="json",
    )

    # Nothing was left to fix, so no suggestion was generated and the run
    # reports an empty fix outcome.
    assert_that(generated).is_empty()
    assert_that(ai_result.fixes_applied).is_equal_to(0)
    assert_that(ai_result.fixes_failed).is_equal_to(0)
    assert_that(ai_result.unfixed_issues).is_equal_to(0)


def test_run_ai_enhancement_fix_action_uses_fresh_rerun_results_for_post_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The post-fix summary is built from the verified per-tool results.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    result = ToolResult(
        name="ruff",
        success=False,
        issues_count=1,
        issues=[
            MockIssue(
                file="src/main.py",
                line=1,
                message="Use of assert",
                code="B101",
            ),
        ],
    )
    suggestion = AIFixSuggestion(
        file="src/main.py",
        line=1,
        code="B101",
        explanation="Replace assert",
        tool_name="ruff",
    )
    config = LintroConfig(
        ai=AIConfig(
            enabled=True,
            transport=AITransport.API,
            auto_apply=True,
        ).model_dump(),
    )

    recorder = _install_orchestrator_fakes(
        monkeypatch=monkeypatch,
        suggestions=[suggestion],
        applied=[suggestion],
        validation=ValidationResult(
            verified=1,
            unverified=0,
            verified_by_tool={"ruff": 1},
            unverified_by_tool={"ruff": 0},
        ),
    )

    run_ai_enhancement(
        action=Action.FIX,
        all_results=[result],
        lintro_config=config,
        logger=RecordingConsoleLogger(),
        output_format="terminal",
    )

    assert_that(recorder.verify_kwargs).is_length(1)
    assert_that(recorder.post_summary_kwargs).is_length(1)
    remaining = recorder.post_summary_kwargs[0]["remaining_results"]
    assert_that([r.name for r in remaining]).is_equal_to(["ruff"])

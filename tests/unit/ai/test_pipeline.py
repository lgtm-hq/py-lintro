"""Tests for the AI fix pipeline (lintro.ai.pipeline.run_fix_pipeline)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that

from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport
from lintro.ai.models import AIFixSuggestion
from lintro.ai.pipeline import run_fix_pipeline
from lintro.ai.validation import ValidationResult
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.base_issue import BaseIssue
from tests.unit.ai.conftest import (
    MockAIProvider,
    MockIssue,
    RecordingConsoleLogger,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PIPELINE = "lintro.ai.pipeline"


def _make_suggestion(
    *,
    file: str = "src/main.py",
    line: int = 1,
    code: str = "E501",
    tool_name: str = "ruff",
    risk_level: str = "",
    confidence: str = "high",
    explanation: str = "fix",
) -> AIFixSuggestion:
    s = AIFixSuggestion(
        file=file,
        line=line,
        code=code,
        explanation=explanation,
        risk_level=risk_level,
        confidence=confidence,
    )
    s.tool_name = tool_name
    return s


def _default_ai_config(**overrides: object) -> AIConfig:
    defaults: dict[str, object] = {
        "enabled": True,
        "transport": AITransport.API,
        "max_fix_attempts": 20,
    }
    defaults.update(overrides)
    return AIConfig.model_validate(defaults)


def _make_result(name: str, issues: list[MockIssue]) -> ToolResult:
    return ToolResult(
        name=name,
        success=False,
        issues_count=len(issues),
        issues=issues,
    )


def _make_fix_issues(
    result: ToolResult,
    issues: list[MockIssue],
) -> list[tuple[ToolResult, BaseIssue]]:
    return [(result, issue) for issue in issues]


@dataclass
class _PipelineRecorder:
    """Records what the fix pipeline hands to each downstream stage.

    Used instead of ``unittest.mock`` doubles so every assertion reads a list
    the stand-in really appended to rather than mock call bookkeeping (#2315).

    Attributes:
        fix_params: Params object of each ``generate_fixes_from_params`` call.
        apply_batches: Suggestion batch passed positionally to ``apply_fixes``.
        apply_kwargs: Keyword arguments of each ``apply_fixes`` call.
        review_batches: Suggestion batch passed to ``review_fixes_interactive``.
        verify_kwargs: Keyword arguments of each ``verify_fixes`` call.
        post_summary_kwargs: Keyword arguments of each
            ``generate_post_fix_summary`` call.
        stages: Names of the stages that ran, in order.
    """

    fix_params: list[Any] = field(default_factory=list)
    apply_batches: list[list[AIFixSuggestion]] = field(default_factory=list)
    apply_kwargs: list[dict[str, Any]] = field(default_factory=list)
    review_batches: list[list[AIFixSuggestion]] = field(default_factory=list)
    verify_kwargs: list[dict[str, Any]] = field(default_factory=list)
    post_summary_kwargs: list[dict[str, Any]] = field(default_factory=list)
    stages: list[str] = field(default_factory=list)


def _install_pipeline_recorder(
    *,
    monkeypatch: pytest.MonkeyPatch,
    generated: Sequence[Sequence[AIFixSuggestion]] = (),
    applied: Sequence[AIFixSuggestion] | None = None,
    reviewed: tuple[int, int, list[AIFixSuggestion]] | None = None,
    validation: ValidationResult | None = None,
) -> _PipelineRecorder:
    """Replace every pipeline stage with a recording stand-in.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        generated: One suggestion list per ``generate_fixes_from_params``
            call; the last entry repeats if the pipeline asks for more.
        applied: Suggestions ``apply_fixes`` reports as applied. ``None``
            means "whatever batch it was given".
        reviewed: Tuple ``review_fixes_interactive`` returns.
        validation: Result ``verify_fixes`` returns.

    Returns:
        The recorder the installed stand-ins append to.
    """
    recorder = _PipelineRecorder()
    batches = [list(batch) for batch in generated]
    review_result = (0, 0, []) if reviewed is None else reviewed

    async def _generate_fixes(
        _issues: object,
        _provider: object,
        params: object,
    ) -> list[AIFixSuggestion]:
        recorder.stages.append("generate")
        recorder.fix_params.append(params)
        if not batches:
            return []
        index = min(len(recorder.fix_params) - 1, len(batches) - 1)
        return list(batches[index])

    def _apply_fixes(
        candidates: Sequence[AIFixSuggestion],
        **kwargs: Any,
    ) -> list[AIFixSuggestion]:
        recorder.stages.append("apply")
        recorder.apply_batches.append(list(candidates))
        recorder.apply_kwargs.append(kwargs)
        return list(candidates) if applied is None else list(applied)

    def _review_interactive(
        candidates: Sequence[AIFixSuggestion],
        *_args: Any,
        **_kwargs: Any,
    ) -> tuple[int, int, list[AIFixSuggestion]]:
        recorder.stages.append("review")
        recorder.review_batches.append(list(candidates))
        return review_result

    def _verify_fixes(**kwargs: Any) -> ValidationResult | None:
        recorder.stages.append("verify")
        recorder.verify_kwargs.append(kwargs)
        return validation

    async def _post_fix_summary(**kwargs: Any) -> None:
        recorder.stages.append("post_summary")
        recorder.post_summary_kwargs.append(kwargs)
        return None

    monkeypatch.setattr(f"{_PIPELINE}.generate_fixes_from_params", _generate_fixes)
    monkeypatch.setattr(f"{_PIPELINE}.apply_fixes", _apply_fixes)
    monkeypatch.setattr(f"{_PIPELINE}.review_fixes_interactive", _review_interactive)
    monkeypatch.setattr(f"{_PIPELINE}.verify_fixes", _verify_fixes)
    monkeypatch.setattr(f"{_PIPELINE}.generate_post_fix_summary", _post_fix_summary)
    monkeypatch.setattr(f"{_PIPELINE}.render_summary", lambda *_a, **_k: "")
    monkeypatch.setattr(f"{_PIPELINE}.render_validation", lambda *_a, **_k: "")
    return recorder


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_budget_tracking_across_multiple_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fix budget shrinks by the issues each tool already consumed.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    issue_a = MockIssue(file="a.py", line=1, code="E501", message="err")
    issue_b = MockIssue(file="b.py", line=1, code="E501", message="err")
    issue_c = MockIssue(file="c.py", line=1, code="W001", message="err")

    result_ruff = _make_result("ruff", [issue_a, issue_b])
    result_mypy = _make_result("mypy", [issue_c])

    fix_issues = _make_fix_issues(result_ruff, [issue_a, issue_b]) + _make_fix_issues(
        result_mypy,
        [issue_c],
    )

    suggestion_a = _make_suggestion(file="a.py", tool_name="ruff")
    suggestion_b = _make_suggestion(file="b.py", tool_name="ruff")
    suggestion_c = _make_suggestion(file="c.py", tool_name="mypy", code="W001")

    recorder = _install_pipeline_recorder(
        monkeypatch=monkeypatch,
        generated=[[suggestion_a, suggestion_b], [suggestion_c]],
        applied=[],
        validation=ValidationResult(),
    )

    await run_fix_pipeline(
        fix_issues=fix_issues,
        provider=MockAIProvider(),
        ai_config=_default_ai_config(max_fix_attempts=3),
        logger=RecordingConsoleLogger(),
        output_format="terminal",
        workspace_root=Path("/tmp"),
    )

    # The first tool sees the full budget of 3; the second sees what the two
    # ruff issues left behind.
    budgets = [params.max_issues for params in recorder.fix_params]
    assert_that(budgets).is_equal_to([3, 1])


async def test_safe_vs_risky_suggestion_splitting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the safe-style suggestion reaches the auto-apply fast path.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    issue = MockIssue(file="a.py", line=1, code="E501", message="err")
    result = _make_result("ruff", [issue])
    fix_issues = _make_fix_issues(result, [issue])

    safe = _make_suggestion(code="E501", risk_level="safe-style")
    risky = _make_suggestion(code="B101", risk_level="behavioral-risk")

    recorder = _install_pipeline_recorder(
        monkeypatch=monkeypatch,
        generated=[[safe, risky]],
        applied=[safe],
        validation=ValidationResult(),
    )
    monkeypatch.setattr(
        f"{_PIPELINE}.is_safe_style_fix",
        lambda suggestion: suggestion.risk_level == "safe-style",
    )

    await run_fix_pipeline(
        fix_issues=fix_issues,
        provider=MockAIProvider(),
        ai_config=_default_ai_config(auto_apply_safe_fixes=True),
        logger=RecordingConsoleLogger(),
        output_format="json",
        workspace_root=Path("/tmp"),
    )

    assert_that(recorder.apply_batches).is_not_empty()
    fast_path_batch = recorder.apply_batches[0]
    assert_that(fast_path_batch).is_length(1)
    assert_that(fast_path_batch[0].risk_level).is_equal_to("safe-style")


async def test_auto_apply_fast_path_json_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """JSON mode auto-applies safe fixes and never prompts for review.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    issue = MockIssue(file="a.py", line=1, code="E501", message="err")
    result = _make_result("ruff", [issue])
    fix_issues = _make_fix_issues(result, [issue])

    safe = _make_suggestion(code="E501", risk_level="safe-style", confidence="high")

    recorder = _install_pipeline_recorder(
        monkeypatch=monkeypatch,
        generated=[[safe]],
        applied=[safe],
        validation=ValidationResult(),
    )

    applied_count, failed_count, suggestions = await run_fix_pipeline(
        fix_issues=fix_issues,
        provider=MockAIProvider(),
        ai_config=_default_ai_config(auto_apply_safe_fixes=True, auto_apply=False),
        logger=RecordingConsoleLogger(),
        output_format="json",
        workspace_root=Path("/tmp"),
    )

    assert_that(applied_count).is_equal_to(1)
    assert_that(failed_count).is_equal_to(0)
    assert_that(suggestions).is_equal_to([safe])
    assert_that(recorder.apply_kwargs).is_not_empty()
    assert_that(recorder.apply_kwargs[0]["auto_apply"]).is_true()
    # JSON mode is non-interactive: the review stage must never run.
    assert_that(recorder.stages).does_not_contain("review")


async def test_interactive_review_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A terminal run without auto-apply routes suggestions through review.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    issue = MockIssue(file="a.py", line=1, code="B101", message="err")
    result = _make_result("ruff", [issue])
    fix_issues = _make_fix_issues(result, [issue])

    suggestion = _make_suggestion(code="B101", risk_level="behavioral-risk")

    recorder = _install_pipeline_recorder(
        monkeypatch=monkeypatch,
        generated=[[suggestion]],
        reviewed=(1, 0, [suggestion]),
        validation=ValidationResult(),
    )
    monkeypatch.setattr(f"{_PIPELINE}.sys.stdin.isatty", lambda: True)

    applied_count, _failed, _suggestions = await run_fix_pipeline(
        fix_issues=fix_issues,
        provider=MockAIProvider(),
        ai_config=_default_ai_config(auto_apply=False, auto_apply_safe_fixes=False),
        logger=RecordingConsoleLogger(),
        output_format="terminal",
        workspace_root=Path("/tmp"),
    )

    assert_that(applied_count).is_equal_to(1)
    assert_that(recorder.review_batches).is_length(1)
    reviewed_codes = [s.code for s in recorder.review_batches[0]]
    assert_that(reviewed_codes).is_equal_to(["B101"])


async def test_no_suggestions_returns_early(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No generated suggestion means no apply, review, verify or summary stage.

    Args:
        monkeypatch: Pytest monkeypatch fixture, used to install recording
            stand-ins for the downstream pipeline stages.
    """
    issue = MockIssue(file="a.py", line=1, code="E501", message="err")
    result = _make_result("ruff", [issue])
    fix_issues = _make_fix_issues(result, [issue])

    stages_run: list[str] = []

    def _record(name: str) -> Callable[..., Any]:
        """Build a stand-in that records that its stage ran.

        Args:
            name: Pipeline stage name.

        Returns:
            Callable[..., Any]: A stand-in returning ``None``.
        """

        def _stage(*_args: Any, **_kwargs: Any) -> None:
            stages_run.append(name)

        return _stage

    async def _no_suggestions(*_args: Any, **_kwargs: Any) -> list[AIFixSuggestion]:
        """Produce no fix suggestions at all.

        Args:
            *_args: Ignored positional extras.
            **_kwargs: Ignored keyword extras.

        Returns:
            list[AIFixSuggestion]: Always empty.
        """
        stages_run.append("generate")
        return []

    monkeypatch.setattr(f"{_PIPELINE}.generate_fixes_from_params", _no_suggestions)
    for stage in (
        "apply_fixes",
        "review_fixes_interactive",
        "verify_fixes",
        "generate_post_fix_summary",
        "render_summary",
        "render_validation",
    ):
        monkeypatch.setattr(f"{_PIPELINE}.{stage}", _record(stage))

    applied, failed, suggestions = await run_fix_pipeline(
        fix_issues=fix_issues,
        provider=MockAIProvider(),
        ai_config=_default_ai_config(),
        logger=RecordingConsoleLogger(),
        output_format="json",
        workspace_root=Path("/tmp"),
    )

    assert_that(applied).is_equal_to(0)
    assert_that(failed).is_equal_to(0)
    assert_that(suggestions).is_empty()
    assert_that(stages_run).is_equal_to(["generate"])


async def test_post_fix_summary_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Applied fixes outside JSON mode trigger the post-fix summary stage.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    issue = MockIssue(file="a.py", line=1, code="B101", message="err")
    result = _make_result("ruff", [issue])
    fix_issues = _make_fix_issues(result, [issue])

    suggestion = _make_suggestion(code="B101", tool_name="ruff")

    recorder = _install_pipeline_recorder(
        monkeypatch=monkeypatch,
        generated=[[suggestion]],
        applied=[suggestion],
        validation=ValidationResult(
            verified=1,
            unverified=0,
            verified_by_tool={"ruff": 1},
            unverified_by_tool={"ruff": 0},
        ),
    )

    applied_count, _failed, _suggestions = await run_fix_pipeline(
        fix_issues=fix_issues,
        provider=MockAIProvider(),
        ai_config=_default_ai_config(auto_apply=True),
        logger=RecordingConsoleLogger(),
        output_format="terminal",
        workspace_root=Path("/tmp"),
    )

    assert_that(applied_count).is_equal_to(1)
    assert_that(recorder.post_summary_kwargs).is_length(1)
    summary_kwargs = recorder.post_summary_kwargs[0]
    assert_that(summary_kwargs["applied"]).is_equal_to(1)
    assert_that(summary_kwargs["rejected"]).is_equal_to(0)
    remaining = [r.name for r in summary_kwargs["remaining_results"]]
    assert_that(remaining).is_equal_to(["ruff"])


async def test_verify_fixes_flow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Applied fixes are handed to verification with their owning tool.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    issue = MockIssue(file="a.py", line=1, code="B101", message="err")
    result = _make_result("ruff", [issue])
    fix_issues = _make_fix_issues(result, [issue])

    suggestion = _make_suggestion(code="B101", tool_name="ruff")

    recorder = _install_pipeline_recorder(
        monkeypatch=monkeypatch,
        generated=[[suggestion]],
        applied=[suggestion],
        validation=ValidationResult(
            verified=1,
            unverified=0,
            verified_by_tool={"ruff": 1},
            unverified_by_tool={"ruff": 0},
        ),
    )

    await run_fix_pipeline(
        fix_issues=fix_issues,
        provider=MockAIProvider(),
        ai_config=_default_ai_config(auto_apply=True),
        logger=RecordingConsoleLogger(),
        output_format="terminal",
        workspace_root=Path("/tmp"),
    )

    assert_that(recorder.verify_kwargs).is_length(1)
    verify_kwargs = recorder.verify_kwargs[0]
    assert_that(verify_kwargs["applied_suggestions"]).is_equal_to([suggestion])
    assert_that(list(verify_kwargs["by_tool"])).is_equal_to(["ruff"])

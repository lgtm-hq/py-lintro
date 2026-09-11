"""Tests for AI orchestration edge cases, error handling, and fail_on_unfixed."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport
from lintro.ai.models import AIFixSuggestion, AIResult, AISummary
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

# ---------------------------------------------------------------------------
# TestAIResultExitCode
# ---------------------------------------------------------------------------


@patch("lintro.ai.orchestrator.require_ai")
@patch("lintro.ai.orchestrator.get_provider")
@patch("lintro.ai.orchestrator.generate_summary")
def test_ai_result_default_no_error(
    mock_generate_summary,
    mock_get_provider,
    _mock_require_ai,
):
    """Default behavior: AI returns AIResult with no error flag."""
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
        ai=AIConfig(enabled=True, transport=AITransport.API).model_dump(),
    )
    logger = MagicMock()

    mock_get_provider.return_value = MockAIProvider()
    mock_generate_summary.return_value = AISummary(overview="AI overview")

    ai_result = run_ai_enhancement(
        action=Action.CHECK,
        all_results=[result],
        lintro_config=config,
        logger=logger,
        output_format="json",
    )

    assert_that(ai_result).is_instance_of(AIResult)
    assert_that(ai_result.error).is_false()
    assert_that(ai_result.fixes_applied).is_equal_to(0)
    assert_that(ai_result.fixes_failed).is_equal_to(0)
    assert_that(ai_result.unfixed_issues).is_equal_to(0)
    assert_that(ai_result.budget_exceeded).is_false()


@patch("lintro.ai.orchestrator.require_ai")
@patch("lintro.ai.orchestrator.get_provider")
@patch("lintro.ai.orchestrator.generate_summary")
@patch("lintro.ai.pipeline.generate_fixes_from_params")
@patch(
    "lintro.ai.orchestrator._resolve_issue_path",
    side_effect=lambda *, file, workspace_root, cwd: Path(file),
)
def test_ai_result_unfixed_issues_when_fixes_fail(
    _mock_normalize,
    mock_generate_fixes,
    mock_generate_summary,
    mock_get_provider,
    _mock_require_ai,
):
    """AIResult reports unfixed issues when fix generation returns nothing."""
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
            fail_on_unfixed=True,
        ).model_dump(),
    )
    logger = MagicMock()

    mock_get_provider.return_value = MockAIProvider()
    mock_generate_summary.return_value = None
    mock_generate_fixes.return_value = []

    ai_result = run_ai_enhancement(
        action=Action.CHECK,
        all_results=[result],
        lintro_config=config,
        logger=logger,
        output_format="json",
        ai_fix=True,
    )

    assert_that(ai_result).is_instance_of(AIResult)
    assert_that(ai_result.unfixed_issues).is_equal_to(1)
    assert_that(ai_result.fixes_applied).is_equal_to(0)


def test_ai_result_error_on_exception():
    """AIResult.error is True when AI enhancement raises an exception."""
    config = LintroConfig(
        ai=AIConfig(enabled=True, transport=AITransport.API).model_dump(),
    )
    logger = MagicMock()

    with patch(
        "lintro.ai.orchestrator.require_ai",
        side_effect=RuntimeError("boom"),
    ):
        ai_result = run_ai_enhancement(
            action=Action.CHECK,
            all_results=[],
            lintro_config=config,
            logger=logger,
            output_format="json",
        )

    assert_that(ai_result).is_instance_of(AIResult)
    assert_that(ai_result.error).is_true()


def test_unset_provider_skips_enhancement_without_fail_on_ai_error() -> None:
    """Chk treats an unset provider as an enhancement skip, not a crash."""
    config = LintroConfig(
        ai=AIConfig(enabled=True, lint=True).model_dump(),
    )
    logger = MagicMock()

    with patch("lintro.ai.orchestrator.require_ai"):
        ai_result = run_ai_enhancement(
            action=Action.CHECK,
            all_results=[],
            lintro_config=config,
            logger=logger,
            output_format="json",
        )

    assert_that(ai_result).is_instance_of(AIResult)
    assert_that(ai_result.error).is_true()


def test_unset_provider_reraises_when_fail_on_ai_error() -> None:
    """fail_on_ai_error re-raises the required-provider configuration error."""
    from lintro.ai.exceptions import AIProviderRequiredError

    config = LintroConfig(
        ai=AIConfig(
            enabled=True,
            lint=True,
            fail_on_ai_error=True,
        ).model_dump(),
    )
    logger = MagicMock()

    with (
        patch("lintro.ai.orchestrator.require_ai"),
        pytest.raises(AIProviderRequiredError, match="ai.provider is required"),
    ):
        run_ai_enhancement(
            action=Action.CHECK,
            all_results=[],
            lintro_config=config,
            logger=logger,
            output_format="json",
        )


def test_ai_result_error_propagates_when_fail_on_ai_error():
    """Exceptions propagate when fail_on_ai_error=True."""
    config = LintroConfig(
        ai=AIConfig(
            enabled=True,
            transport=AITransport.API,
            fail_on_ai_error=True,
        ).model_dump(),
    )
    logger = MagicMock()

    with (
        patch(
            "lintro.ai.orchestrator.require_ai",
            side_effect=RuntimeError("boom"),
        ),
        pytest.raises(RuntimeError, match="boom"),
    ):
        run_ai_enhancement(
            action=Action.CHECK,
            all_results=[],
            lintro_config=config,
            logger=logger,
            output_format="json",
        )


@patch("lintro.ai.orchestrator.require_ai")
@patch("lintro.ai.orchestrator.get_provider")
@patch("lintro.ai.pipeline.generate_fixes_from_params")
@patch("lintro.ai.pipeline.apply_fixes")
@patch("lintro.ai.pipeline.verify_fixes")
@patch(
    "lintro.ai.orchestrator._resolve_issue_path",
    side_effect=lambda *, file, workspace_root, cwd: Path(file),
)
def test_ai_result_tracks_applied_fixes(
    _mock_normalize,
    mock_verify_fixes,
    mock_apply_fixes,
    mock_generate_fixes,
    mock_get_provider,
    _mock_require_ai,
):
    """AIResult correctly reports fixes_applied and fixes_failed."""
    result = ToolResult(
        name="ruff",
        success=False,
        issues_count=2,
        issues=[
            MockIssue(
                file="src/main.py",
                line=1,
                message="Use of assert",
                code="B101",
            ),
            MockIssue(
                file="src/main.py",
                line=2,
                message="Line too long",
                code="E501",
            ),
        ],
        remaining_issues_count=2,
    )
    suggestion1 = AIFixSuggestion(
        file="src/main.py",
        line=1,
        code="B101",
        explanation="Replace assert",
        tool_name="ruff",
    )
    suggestion2 = AIFixSuggestion(
        file="src/main.py",
        line=2,
        code="E501",
        explanation="Break line",
        tool_name="ruff",
    )
    config = LintroConfig(
        ai=AIConfig(
            enabled=True,
            transport=AITransport.API,
            auto_apply=True,
        ).model_dump(),
    )
    logger = MagicMock()

    mock_get_provider.return_value = MockAIProvider()
    mock_generate_fixes.return_value = [suggestion1, suggestion2]
    # Only one fix applies successfully
    mock_apply_fixes.return_value = [suggestion1]
    mock_verify_fixes.return_value = ValidationResult(
        verified=1,
        unverified=0,
        verified_by_tool={"ruff": 1},
        unverified_by_tool={"ruff": 0},
    )

    ai_result = run_ai_enhancement(
        action=Action.FIX,
        all_results=[result],
        lintro_config=config,
        logger=logger,
        output_format="json",
    )

    assert_that(ai_result).is_instance_of(AIResult)
    assert_that(ai_result.fixes_applied).is_equal_to(1)
    assert_that(ai_result.fixes_failed).is_equal_to(1)
    assert_that(ai_result.unfixed_issues).is_equal_to(1)


# ---------------------------------------------------------------------------
# TestFailOnUnfixed
# ---------------------------------------------------------------------------


def test_fail_on_unfixed_config_default_is_false():
    """Verify fail_on_unfixed defaults to False."""
    config = AIConfig()
    assert_that(config.fail_on_unfixed).is_false()


def test_fail_on_unfixed_config_can_be_set():
    """Verify fail_on_unfixed can be set to True."""
    config = AIConfig(fail_on_unfixed=True)
    assert_that(config.fail_on_unfixed).is_true()


def _run_check_with_provider_error(exc: Exception) -> RecordingConsoleLogger:
    """Run a check whose provider lookup raises, returning the console log.

    Args:
        exc: Exception raised in place of the provider call.

    Returns:
        The recording console logger the orchestrator wrote to.
    """
    config = LintroConfig(
        ai=AIConfig(enabled=True, transport=AITransport.CLI).model_dump(),
    )
    logger = RecordingConsoleLogger()

    with (
        patch("lintro.ai.orchestrator.require_ai"),
        patch("lintro.ai.orchestrator.get_provider", side_effect=exc),
    ):
        ai_result = run_ai_enhancement(
            action=Action.CHECK,
            all_results=[],
            lintro_config=config,
            logger=logger,
            output_format="auto",
        )

    assert_that(ai_result.error).is_true()
    return logger


def test_provider_error_message_reaches_the_console():
    """The swallowed provider message is printed, not just its class (#2573).

    A rejected request schema surfaced only as ``AI: enhancement unavailable
    (KeyError)``, which told the user nothing about the cause.
    """
    logger = _run_check_with_provider_error(
        RuntimeError(
            "tools.0.custom.input_schema.type: Input should be 'object'",
        ),
    )

    assert_that(logger.text).contains("AI: enhancement unavailable")
    assert_that(logger.text).contains("RuntimeError")
    assert_that(logger.text).contains("input_schema.type")


#: A credential in the shape ``lintro/ai/secrets.py`` actually matches
#: (``sk-`` followed by 20 or more alphanumerics). The test below asserts on
#: this literal, so it fails if ``_failure_detail`` stops redacting.
_FAKE_API_KEY = "sk-Hk39dQ2mVb71PzLt58Rn"


def test_provider_error_message_is_redacted_and_single_line():
    """Only the first line is shown, with secrets redacted (#2573)."""
    logger = _run_check_with_provider_error(
        RuntimeError(
            f"auth failed for key {_FAKE_API_KEY}\nsecond line with detail",
        ),
    )

    assert_that(logger.text).does_not_contain(_FAKE_API_KEY)
    assert_that(logger.text).contains("[REDACTED]")
    assert_that(logger.text).does_not_contain("second line with detail")


def test_provider_error_without_message_shows_the_class_name():
    """An exception carrying no message degrades to the class name (#2573)."""
    logger = _run_check_with_provider_error(RuntimeError())

    assert_that(logger.text).contains("AI: enhancement unavailable (RuntimeError)")

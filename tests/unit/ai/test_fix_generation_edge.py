"""Tests for fix generation edge cases, error handling, and retries.

Covers provider errors, concurrent generation, retry behaviour,
and authentication error handling.
"""

from __future__ import annotations

import json

from assertpy import assert_that

from lintro.ai.fix import (
    generate_fixes,
)
from lintro.ai.providers.base import AIResponse
from tests.unit.ai.conftest import MockAIProvider, MockIssue


def _make_ai_response(
    original: str = "x = 1",
    suggested: str = "x = 2",
    explanation: str = "Fix",
    confidence: str = "high",
    risk_level: str | None = None,
) -> AIResponse:
    """Helper to build a successful AIResponse with a valid JSON payload."""
    payload: dict[str, str | None] = {
        "original_code": original,
        "suggested_code": suggested,
        "explanation": explanation,
        "confidence": confidence,
    }
    if risk_level is not None:
        payload["risk_level"] = risk_level
    return AIResponse(
        content=json.dumps(payload),
        model="mock",
        input_tokens=10,
        output_tokens=10,
        cost_estimate=0.001,
        provider="mock",
    )


# ---------------------------------------------------------------------------
# Provider error handling
# ---------------------------------------------------------------------------


def test_generate_fixes_handles_provider_error(tmp_path):
    """Verify that a provider exception results in an empty fix list."""
    source = tmp_path / "test.py"
    source.write_text("x = 1\n")

    issue = MockIssue(
        file=str(source),
        line=1,
        code="B101",
        message="test",
    )

    class ErrorProvider(MockAIProvider):
        def complete(self, prompt, **kwargs):
            raise RuntimeError("API down")

    provider = ErrorProvider()
    result = generate_fixes(
        [issue],
        provider,
        tool_name="ruff",
    )

    assert_that(result).is_empty()


# ---------------------------------------------------------------------------
# Concurrent generate_fixes (ThreadPoolExecutor path)
# ---------------------------------------------------------------------------


def test_concurrent_generation_with_multiple_workers(tmp_path):
    """generate_fixes with max_workers=3 exercises the ThreadPoolExecutor path."""
    # Use separate files so batching does not group them,
    # exercising the ThreadPoolExecutor path for single-issue calls.
    sources = []
    for i in range(1, 4):
        f = tmp_path / f"test{i}.py"
        f.write_text(f"line{i}\n")
        sources.append(f)

    issues = [
        MockIssue(
            file=str(sources[i - 1]),
            line=1,
            code="B101",
            message=f"Issue {i}",
        )
        for i in range(1, 4)
    ]

    responses = [
        AIResponse(
            content=json.dumps(
                {
                    "original_code": f"line{i}",
                    "suggested_code": f"fixed_line{i}",
                    "explanation": f"Fix issue {i}",
                    "confidence": "high",
                },
            ),
            model="mock",
            input_tokens=10,
            output_tokens=10,
            cost_estimate=0.001,
            provider="mock",
        )
        for i in range(1, 4)
    ]
    provider = MockAIProvider(responses=responses)

    result = generate_fixes(
        issues,
        provider,
        tool_name="ruff",
        max_workers=3,
        workspace_root=tmp_path,
    )

    assert_that(provider.calls).is_length(3)
    assert_that(result).is_length(3)


def test_concurrent_mixed_success_and_failure(tmp_path):
    """Concurrent mode: one success, one failure -> 1 suggestion returned."""
    # Use separate files so batching does not group them
    source1 = tmp_path / "test1.py"
    source1.write_text("line1\n")
    source2 = tmp_path / "test2.py"
    source2.write_text("line2\n")

    issues = [
        MockIssue(
            file=str(source1),
            line=1,
            code="B101",
            message="Issue 1",
        ),
        MockIssue(
            file=str(source2),
            line=1,
            code="B101",
            message="Issue 2",
        ),
    ]

    responses = [
        AIResponse(
            content=json.dumps(
                {
                    "original_code": "line1",
                    "suggested_code": "fixed_line1",
                    "explanation": "Fix issue 1",
                    "confidence": "high",
                },
            ),
            model="mock",
            input_tokens=10,
            output_tokens=10,
            cost_estimate=0.001,
            provider="mock",
        ),
        AIResponse(
            content="not-json",
            model="mock",
            input_tokens=10,
            output_tokens=10,
            cost_estimate=0.001,
            provider="mock",
        ),
    ]
    provider = MockAIProvider(responses=responses)

    result = generate_fixes(
        issues,
        provider,
        tool_name="ruff",
        max_workers=3,
        workspace_root=tmp_path,
    )

    assert_that(provider.calls).is_length(2)
    assert_that(result).is_length(1)


# ---------------------------------------------------------------------------
# _call_provider retry behaviour (via with_retry)
# ---------------------------------------------------------------------------


def test_retries_on_provider_error(tmp_path):
    """Transient AIProviderError triggers retries, then succeeds."""
    from lintro.ai.exceptions import AIProviderError

    source = tmp_path / "test.py"
    source.write_text("x = 1\n")

    issue = MockIssue(
        file=str(source),
        line=1,
        code="B101",
        message="test",
    )

    call_count = {"n": 0}
    success_response = _make_ai_response()

    class RetryProvider(MockAIProvider):
        def complete(self, prompt, **kwargs):
            call_count["n"] += 1
            if call_count["n"] < 3:
                raise AIProviderError("transient")
            return success_response

    provider = RetryProvider()
    result = generate_fixes(
        [issue],
        provider,
        tool_name="ruff",
        workspace_root=tmp_path,
        max_retries=3,
    )

    assert_that(call_count["n"]).is_equal_to(3)
    assert_that(result).is_length(1)


def test_no_retry_on_auth_error(tmp_path):
    """AIAuthenticationError is never retried."""
    from lintro.ai.exceptions import AIAuthenticationError

    source = tmp_path / "test.py"
    source.write_text("x = 1\n")

    issue = MockIssue(
        file=str(source),
        line=1,
        code="B101",
        message="test",
    )

    call_count = {"n": 0}

    class AuthErrorProvider(MockAIProvider):
        def complete(self, prompt, **kwargs):
            call_count["n"] += 1
            raise AIAuthenticationError("bad key")

    provider = AuthErrorProvider()
    result = generate_fixes(
        [issue],
        provider,
        tool_name="ruff",
        workspace_root=tmp_path,
        max_retries=3,
    )

    # Auth errors propagate immediately -- only 1 call, no retries
    assert_that(call_count["n"]).is_equal_to(1)
    assert_that(result).is_empty()


def test_max_retries_zero_means_no_retry(tmp_path):
    """max_retries=0 means the provider is called exactly once."""
    source = tmp_path / "test.py"
    source.write_text("x = 1\n")

    issue = MockIssue(
        file=str(source),
        line=1,
        code="B101",
        message="test",
    )

    provider = MockAIProvider()
    generate_fixes(
        [issue],
        provider,
        tool_name="ruff",
        workspace_root=tmp_path,
        max_retries=0,
    )

    assert_that(provider.calls).is_length(1)

"""Tests for AI fix generation service."""

from __future__ import annotations

import json
import os

import pytest
from assertpy import assert_that

from lintro.ai.fix import (
    _extract_context,
    _generate_diff,
    _parse_fix_response,
    _read_file_safely,
    generate_fixes,
)
from lintro.ai.providers.base import AIResponse
from lintro.models.core.tool_result import ToolResult
from tests.unit.ai.conftest import MockAIProvider, MockIssue

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def source_file(tmp_path):
    """Create a minimal Python source file and return its path."""
    f = tmp_path / "test.py"
    f.write_text("x = 1\n")
    return f


@pytest.fixture()
def single_issue(source_file):
    """Return a single MockIssue pointing at the source file."""
    return MockIssue(
        file=str(source_file),
        line=1,
        code="B101",
        message="test",
    )


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
# _read_file_safely
# ---------------------------------------------------------------------------


def test_read_file_safely_reads_existing_file(tmp_path):
    """Existing file contents are returned as a string."""
    f = tmp_path / "test.py"
    f.write_text("hello world")
    result = _read_file_safely(str(f))
    assert_that(result).is_equal_to("hello world")


def test_read_file_safely_returns_none_for_missing():
    """Missing file returns None instead of raising."""
    result = _read_file_safely("/nonexistent/file.py")
    assert_that(result).is_none()


# ---------------------------------------------------------------------------
# _extract_context
# ---------------------------------------------------------------------------


def test_extract_context_extracts_context():
    """Context window is centred on the target line."""
    content = "\n".join(f"line {i}" for i in range(1, 31))
    context, start, end = _extract_context(content, 15, 5)
    assert_that(start).is_equal_to(10)
    assert_that(end).is_equal_to(20)
    assert_that(context).contains("line 15")


def test_extract_context_clamps_to_start():
    """Verify context window clamps to the first line when target is near the start."""
    content = "\n".join(f"line {i}" for i in range(1, 11))
    context, start, end = _extract_context(content, 1, 5)
    assert_that(start).is_equal_to(1)


def test_extract_context_clamps_to_end():
    """Verify context window clamps to the last line when target is near the end."""
    content = "\n".join(f"line {i}" for i in range(1, 11))
    context, start, end = _extract_context(content, 10, 5)
    assert_that(end).is_equal_to(10)


# ---------------------------------------------------------------------------
# _generate_diff
# ---------------------------------------------------------------------------


def test_generate_diff_generates_unified_diff():
    """Verify unified diff output contains expected file headers and change markers."""
    diff = _generate_diff("test.py", "old code\n", "new code\n")
    assert_that(diff).contains("a/test.py")
    assert_that(diff).contains("b/test.py")
    assert_that(diff).contains("-old code")
    assert_that(diff).contains("+new code")


def test_generate_diff_no_diff_for_identical():
    """Verify that identical content produces an empty diff string."""
    diff = _generate_diff("test.py", "same\n", "same\n")
    assert_that(diff).is_equal_to("")


# ---------------------------------------------------------------------------
# _parse_fix_response
# ---------------------------------------------------------------------------


def test_parse_fix_response_valid_response():
    """Valid JSON is parsed into a fix suggestion with correct fields."""
    content = json.dumps(
        {
            "original_code": "assert x > 0",
            "suggested_code": "if not x > 0:\n    raise ValueError",
            "explanation": "Replace assert",
            "confidence": "high",
        },
    )
    result = _parse_fix_response(content, "main.py", 10, "B101")
    assert_that(result).is_not_none()
    assert result is not None
    assert_that(result.file).is_equal_to("main.py")
    assert_that(result.confidence).is_equal_to("high")
    assert_that(result.diff).is_not_empty()


def test_parse_fix_response_invalid_json():
    """Verify that invalid JSON content returns None."""
    result = _parse_fix_response("not json", "main.py", 10, "B101")
    assert_that(result).is_none()


def test_parse_fix_response_identical_code():
    """Verify that identical original and suggested code returns None."""
    content = json.dumps(
        {
            "original_code": "x = 1",
            "suggested_code": "x = 1",
            "explanation": "No change",
            "confidence": "high",
        },
    )
    result = _parse_fix_response(content, "main.py", 10, "B101")
    assert_that(result).is_none()


def test_parse_fix_response_empty_original():
    """Verify that an empty original_code field returns None."""
    content = json.dumps(
        {
            "original_code": "",
            "suggested_code": "new code",
            "explanation": "Fix",
            "confidence": "medium",
        },
    )
    result = _parse_fix_response(content, "main.py", 10, "B101")
    assert_that(result).is_none()


def test_parse_fix_response_extracts_risk_level():
    """_parse_fix_response should populate risk_level from the JSON payload."""
    content = json.dumps(
        {
            "original_code": "assert x > 0",
            "suggested_code": "if not x > 0:\n    raise ValueError",
            "explanation": "Replace assert",
            "confidence": "high",
            "risk_level": "low",
        },
    )
    result = _parse_fix_response(content, "main.py", 10, "B101")
    assert_that(result).is_not_none()
    assert result is not None
    assert_that(result.risk_level).is_equal_to("low")


def test_parse_fix_response_risk_level_defaults_to_empty():
    """When risk_level is absent from the JSON, the field should default to ''."""
    content = json.dumps(
        {
            "original_code": "assert x > 0",
            "suggested_code": "if not x > 0:\n    raise ValueError",
            "explanation": "Replace assert",
            "confidence": "high",
        },
    )
    result = _parse_fix_response(content, "main.py", 10, "B101")
    assert_that(result).is_not_none()
    assert result is not None
    assert_that(result.risk_level).is_equal_to("")


# ---------------------------------------------------------------------------
# generate_fixes
# ---------------------------------------------------------------------------


def test_generate_fixes_empty_issues(mock_provider):
    """Verify that an empty issue list returns an empty result."""
    result = generate_fixes(
        [],
        mock_provider,
        tool_name="ruff",
    )
    assert_that(result).is_empty()


def test_generate_fixes_generates_fixes_for_unfixable(tmp_path):
    """Unfixable issues are sent to the AI and produce suggestions."""
    source = tmp_path / "test.py"
    source.write_text("assert x > 0\nprint('hello')\n")

    issue = MockIssue(
        file=str(source),
        line=1,
        code="B101",
        message="Use of assert",
        fixable=False,
    )

    response = AIResponse(
        content=json.dumps(
            {
                "original_code": "assert x > 0",
                "suggested_code": "if x <= 0:\n    raise ValueError",
                "explanation": "Replace assert",
                "confidence": "high",
            },
        ),
        model="mock",
        input_tokens=100,
        output_tokens=50,
        cost_estimate=0.001,
        provider="mock",
    )
    provider = MockAIProvider(responses=[response])

    result = generate_fixes(
        [issue],
        provider,
        tool_name="bandit",
        workspace_root=tmp_path,
    )

    assert_that(result).is_length(1)
    assert_that(result[0].code).is_equal_to("B101")
    assert_that(result[0].diff).is_not_empty()


def test_generate_fixes_processes_fixable_issues(tmp_path):
    """AI should attempt fixes for ALL issues, including fixable ones."""
    source = tmp_path / "test.py"
    source.write_text("x = 1\n")

    issue = MockIssue(
        file=str(source),
        line=1,
        code="E501",
        message="Line too long",
        fixable=True,
    )

    provider = MockAIProvider()
    generate_fixes(
        [issue],
        provider,
        tool_name="ruff",
        workspace_root=tmp_path,
    )

    assert_that(provider.calls).is_not_empty()


def test_generate_fixes_skips_issues_without_file(mock_provider):
    """Verify that issues without a file path are skipped."""
    issue = MockIssue(line=1, code="B101", message="test")
    result = generate_fixes(
        [issue],
        mock_provider,
        tool_name="ruff",
    )
    assert_that(result).is_empty()


def test_generate_fixes_respects_max_issues(tmp_path):
    """Verify that the max_issues parameter limits the number of provider calls."""
    # Use separate files so batching does not group them
    sources = []
    for i in range(1, 6):
        f = tmp_path / f"test{i}.py"
        f.write_text("x = 1\n" * 50)
        sources.append(f)

    issues = [
        MockIssue(
            file=str(sources[i - 1]),
            line=1,
            code="B101",
            message="test",
        )
        for i in range(1, 6)
    ]

    provider = MockAIProvider()
    generate_fixes(
        issues,
        provider,
        tool_name="ruff",
        max_issues=2,
        workspace_root=tmp_path,
    )

    assert_that(provider.calls).is_length(2)


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


def test_generate_fixes_skips_issues_with_unreadable_relative_paths(tmp_path):
    """Relative paths not resolvable from CWD are silently skipped."""
    subdir = tmp_path / "tools" / "js"
    subdir.mkdir(parents=True)
    source = subdir / "test.js"
    source.write_text("var x = 1;\n")

    issue = MockIssue(
        file="js/test.js",
        line=1,
        code="no-var",
        message="Unexpected var",
    )

    provider = MockAIProvider()
    result = generate_fixes(
        [issue],
        provider,
        tool_name="oxlint",
    )

    assert_that(provider.calls).is_empty()
    assert_that(result).is_empty()


def test_generate_fixes_provider_prompt_uses_workspace_relative_path(tmp_path):
    """Provider prompt contains workspace-relative paths, not absolute."""
    source = tmp_path / "src" / "service.py"
    source.parent.mkdir(parents=True)
    source.write_text("assert ready\n", encoding="utf-8")

    issue = MockIssue(
        file=str(source),
        line=1,
        code="B101",
        message="Use of assert",
    )

    response = AIResponse(
        content=json.dumps(
            {
                "original_code": "assert ready",
                "suggested_code": "if not ready:\n    raise ValueError",
                "explanation": "Replace assert",
                "confidence": "high",
            },
        ),
        model="mock",
        input_tokens=10,
        output_tokens=10,
        cost_estimate=0.001,
        provider="mock",
    )
    provider = MockAIProvider(responses=[response])

    generate_fixes(
        [issue],
        provider,
        tool_name="ruff",
        workspace_root=tmp_path,
        max_tokens=333,
    )

    assert_that(provider.calls).is_length(1)
    prompt = provider.calls[0]["prompt"]
    assert_that(prompt).contains("File: src/service.py")
    assert_that(prompt).does_not_contain(str(source))
    assert_that(provider.calls[0]["max_tokens"]).is_equal_to(333)


def test_generate_fixes_skips_issue_outside_workspace_root(tmp_path):
    """Verify that issues with files outside the workspace root are skipped."""
    outside = tmp_path.parent / "outside.py"
    outside.write_text("assert x\n", encoding="utf-8")

    issue = MockIssue(
        file=str(outside),
        line=1,
        code="B101",
        message="Use of assert",
    )

    provider = MockAIProvider()
    result = generate_fixes(
        [issue],
        provider,
        tool_name="ruff",
        workspace_root=tmp_path,
    )

    assert_that(provider.calls).is_empty()
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
# Timeout propagation
# ---------------------------------------------------------------------------


def test_timeout_reaches_provider(tmp_path):
    """Custom timeout value is passed through to provider.complete()."""
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
        timeout=120.0,
    )

    assert_that(provider.calls).is_length(1)
    assert_that(provider.calls[0]["timeout"]).is_equal_to(120.0)


def test_default_timeout_is_60(tmp_path):
    """Default timeout (60s) is used when no custom value is provided."""
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
    )

    assert_that(provider.calls).is_length(1)
    assert_that(provider.calls[0]["timeout"]).is_equal_to(60.0)


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


# ---------------------------------------------------------------------------
# ToolResult.cwd field
# ---------------------------------------------------------------------------


def test_tool_result_cwd_defaults_to_none():
    """Verify that ToolResult.cwd defaults to None when not specified."""
    result = ToolResult(name="test", success=True)
    assert_that(result.cwd).is_none()


def test_tool_result_cwd_preserves_value():
    """Verify that ToolResult.cwd preserves the value passed at construction."""
    result = ToolResult(name="test", success=True, cwd="/some/path")
    assert_that(result.cwd).is_equal_to("/some/path")


# ---------------------------------------------------------------------------
# Relative path resolution (ToolResult.cwd)
# ---------------------------------------------------------------------------


def test_resolves_relative_paths_with_cwd(tmp_path):
    """Issues with relative paths should be resolved using result.cwd."""
    tool_cwd = tmp_path / "test_samples" / "tools"
    js_dir = tool_cwd / "javascript" / "oxlint"
    js_dir.mkdir(parents=True)
    source = js_dir / "violations.js"
    source.write_text("var x = 1;\n")

    issue = MockIssue(
        file="javascript/oxlint/violations.js",
        line=1,
        code="no-var",
        message="Unexpected var",
    )

    cwd = str(tool_cwd)
    if not os.path.isabs(issue.file):
        issue.file = os.path.join(cwd, issue.file)

    provider = MockAIProvider()
    generate_fixes(
        [issue],
        provider,
        tool_name="oxlint",
        workspace_root=tmp_path,
    )

    assert_that(provider.calls).is_length(1)


def test_absolute_paths_unchanged_by_resolution():
    """Absolute paths should not be modified by path resolution."""
    issue = MockIssue(
        file="/absolute/path/to/file.py",
        line=1,
        code="B101",
        message="test",
    )

    cwd = "/some/other/dir"
    if not os.path.isabs(issue.file):
        issue.file = os.path.join(cwd, issue.file)

    assert_that(issue.file).is_equal_to("/absolute/path/to/file.py")


def test_no_resolution_when_cwd_is_none():
    """When cwd is None, relative paths should remain unchanged."""
    issue = MockIssue(
        file="relative/path/file.js",
        line=1,
        code="no-var",
        message="test",
    )

    cwd = None
    if cwd and not os.path.isabs(issue.file):
        issue.file = os.path.join(cwd, issue.file)

    assert_that(issue.file).is_equal_to("relative/path/file.js")


# ---------------------------------------------------------------------------
# P3-3: Full file context for small files
# ---------------------------------------------------------------------------


def test_full_file_context_for_small_file(tmp_path):
    """Small files should send full content as context (lines 1-N)."""
    source = tmp_path / "small.py"
    source.write_text("x = 1\ny = 2\nz = 3\n")

    issue = MockIssue(
        file=str(source),
        line=2,
        code="E501",
        message="Line too long",
    )

    provider = MockAIProvider()
    generate_fixes(
        [issue],
        provider,
        tool_name="ruff",
        workspace_root=tmp_path,
    )

    assert_that(provider.calls).is_length(1)
    prompt = provider.calls[0]["prompt"]
    # Full file sent: context window should span the entire file
    assert_that(prompt).contains("lines 1-3")
    assert_that(prompt).contains("x = 1")
    assert_that(prompt).contains("z = 3")


def test_full_file_skipped_when_file_exceeds_threshold(tmp_path):
    """Files over full_file_threshold should use windowed context."""
    import threading

    from lintro.ai.fix import _call_provider, _generate_single_fix
    from lintro.ai.retry import with_retry

    # Create a file with 50 lines but set threshold to 5
    source = tmp_path / "big.py"
    source.write_text("\n".join(f"line_{i}" for i in range(1, 51)) + "\n")

    issue = MockIssue(
        file=str(source),
        line=25,
        code="E501",
        message="Line too long",
    )

    provider = MockAIProvider()
    retrying_call = with_retry(max_retries=0)(_call_provider)

    _generate_single_fix(
        issue,
        provider,
        "ruff",
        {},
        threading.Lock(),
        tmp_path,
        2048,
        retrying_call,
        full_file_threshold=5,  # File has 50 lines, above threshold
    )

    assert_that(provider.calls).is_length(1)
    prompt = provider.calls[0]["prompt"]
    # Should NOT contain "lines 1-50" (full file); uses windowed context
    assert_that(prompt).does_not_contain("lines 1-50")
    # Should contain a windowed range around line 25
    assert_that(prompt).contains("line_25")


def test_full_file_skipped_when_over_token_budget(tmp_path):
    """Full file that exceeds token budget should fall back to windowed context."""
    import threading

    from lintro.ai.fix import _call_provider, _generate_single_fix
    from lintro.ai.retry import with_retry

    # Create a small file but set a very tight token budget
    source = tmp_path / "medium.py"
    source.write_text("x = 1\ny = 2\nz = 3\n")

    issue = MockIssue(
        file=str(source),
        line=2,
        code="E501",
        message="Line too long",
    )

    provider = MockAIProvider()
    retrying_call = with_retry(max_retries=0)(_call_provider)

    _generate_single_fix(
        issue,
        provider,
        "ruff",
        {},
        threading.Lock(),
        tmp_path,
        2048,
        retrying_call,
        max_prompt_tokens=10,  # Very tight budget, full file won't fit
    )

    assert_that(provider.calls).is_length(1)


# ---------------------------------------------------------------------------
# P3-1: Multi-issue batching per file
# ---------------------------------------------------------------------------


def test_batch_prompt_for_multi_issue_file(tmp_path):
    """Multiple issues in one file should trigger a batch prompt."""
    source = tmp_path / "multi.py"
    source.write_text("x = 1\ny = 2\nz = 3\n")

    issues = [
        MockIssue(
            file=str(source),
            line=1,
            code="B101",
            message="Issue one",
        ),
        MockIssue(
            file=str(source),
            line=3,
            code="E501",
            message="Issue two",
        ),
    ]

    batch_response = AIResponse(
        content=json.dumps(
            [
                {
                    "line": 1,
                    "code": "B101",
                    "original_code": "x = 1",
                    "suggested_code": "x = 2",
                    "explanation": "Fix one",
                    "confidence": "high",
                    "risk_level": "behavioral-risk",
                },
                {
                    "line": 3,
                    "code": "E501",
                    "original_code": "z = 3",
                    "suggested_code": "z = 4",
                    "explanation": "Fix two",
                    "confidence": "medium",
                    "risk_level": "safe-style",
                },
            ],
        ),
        model="mock",
        input_tokens=50,
        output_tokens=50,
        cost_estimate=0.002,
        provider="mock",
    )
    provider = MockAIProvider(responses=[batch_response])

    result = generate_fixes(
        issues,
        provider,
        tool_name="ruff",
        workspace_root=tmp_path,
    )

    # Only 1 provider call (the batch), not 2 single calls
    assert_that(provider.calls).is_length(1)
    prompt = provider.calls[0]["prompt"]
    assert_that(prompt).contains("Issue one")
    assert_that(prompt).contains("Issue two")
    assert_that(prompt).contains("JSON array")

    assert_that(result).is_length(2)
    assert_that(result[0].line).is_equal_to(1)
    assert_that(result[1].line).is_equal_to(3)
    assert_that(result[0].tool_name).is_equal_to("ruff")


def test_batch_fallback_to_single_on_parse_failure(tmp_path):
    """Failed batch parse falls back to single-issue mode."""
    source = tmp_path / "multi.py"
    source.write_text("x = 1\ny = 2\n")

    issues = [
        MockIssue(
            file=str(source),
            line=1,
            code="B101",
            message="Issue one",
        ),
        MockIssue(
            file=str(source),
            line=2,
            code="E501",
            message="Issue two",
        ),
    ]

    # First response (batch) is invalid, subsequent ones are valid single fixes
    responses = [
        AIResponse(
            content="not-a-json-array",
            model="mock",
            input_tokens=10,
            output_tokens=10,
            cost_estimate=0.001,
            provider="mock",
        ),
        AIResponse(
            content=json.dumps(
                {
                    "original_code": "x = 1",
                    "suggested_code": "x = 2",
                    "explanation": "Fix one",
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
            content=json.dumps(
                {
                    "original_code": "y = 2",
                    "suggested_code": "y = 3",
                    "explanation": "Fix two",
                    "confidence": "high",
                },
            ),
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
        workspace_root=tmp_path,
    )

    # 1 batch call + 2 single fallback calls = 3
    assert_that(provider.calls).is_length(3)
    assert_that(result).is_length(2)


def test_single_issue_file_not_batched(tmp_path):
    """A file with only 1 issue should use the single-issue path."""
    source = tmp_path / "single.py"
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
    )

    assert_that(provider.calls).is_length(1)
    prompt = provider.calls[0]["prompt"]
    # Single-issue prompt, not batch
    assert_that(prompt).does_not_contain("JSON array")
    assert_that(prompt).contains("Error code: B101")


# ---------------------------------------------------------------------------
# _parse_batch_response
# ---------------------------------------------------------------------------


def test_parse_batch_response_valid():
    """Valid batch JSON array is parsed into suggestions."""
    from lintro.ai.fix import _parse_batch_response

    content = json.dumps(
        [
            {
                "line": 5,
                "code": "E501",
                "original_code": "old",
                "suggested_code": "new",
                "explanation": "Fix",
                "confidence": "high",
                "risk_level": "safe-style",
            },
        ],
    )
    result = _parse_batch_response(content, "test.py")
    assert_that(result).is_length(1)
    assert_that(result[0].line).is_equal_to(5)
    assert_that(result[0].code).is_equal_to("E501")
    assert_that(result[0].risk_level).is_equal_to("safe-style")


def test_parse_batch_response_invalid_json():
    """Invalid JSON returns empty list."""
    from lintro.ai.fix import _parse_batch_response

    result = _parse_batch_response("not json", "test.py")
    assert_that(result).is_empty()


def test_parse_batch_response_not_array():
    """Non-array JSON returns empty list."""
    from lintro.ai.fix import _parse_batch_response

    result = _parse_batch_response('{"key": "value"}', "test.py")
    assert_that(result).is_empty()


def test_parse_batch_response_skips_identical_code():
    """Items with identical original and suggested code are skipped."""
    from lintro.ai.fix import _parse_batch_response

    content = json.dumps(
        [
            {
                "line": 1,
                "code": "E501",
                "original_code": "same",
                "suggested_code": "same",
                "explanation": "No change",
                "confidence": "high",
            },
        ],
    )
    result = _parse_batch_response(content, "test.py")
    assert_that(result).is_empty()

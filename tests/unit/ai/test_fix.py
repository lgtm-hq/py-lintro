"""Tests for AI fix generation service."""

from __future__ import annotations

import json
import os

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


class TestReadFileSafely:
    """Tests for _read_file_safely."""

    def test_reads_existing_file(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("hello world")
        result = _read_file_safely(str(f))
        assert_that(result).is_equal_to("hello world")

    def test_returns_none_for_missing(self):
        result = _read_file_safely("/nonexistent/file.py")
        assert_that(result).is_none()


class TestExtractContext:
    """Tests for _extract_context."""

    def test_extracts_context(self):
        content = "\n".join(f"line {i}" for i in range(1, 31))
        context, start, end = _extract_context(content, 15, 5)
        assert_that(start).is_equal_to(10)
        assert_that(end).is_equal_to(20)
        assert_that(context).contains("line 15")

    def test_clamps_to_start(self):
        content = "\n".join(f"line {i}" for i in range(1, 11))
        context, start, end = _extract_context(content, 1, 5)
        assert_that(start).is_equal_to(1)

    def test_clamps_to_end(self):
        content = "\n".join(f"line {i}" for i in range(1, 11))
        context, start, end = _extract_context(content, 10, 5)
        assert_that(end).is_equal_to(10)


class TestGenerateDiff:
    """Tests for _generate_diff."""

    def test_generates_unified_diff(self):
        diff = _generate_diff("test.py", "old code\n", "new code\n")
        assert_that(diff).contains("a/test.py")
        assert_that(diff).contains("b/test.py")
        assert_that(diff).contains("-old code")
        assert_that(diff).contains("+new code")

    def test_no_diff_for_identical(self):
        diff = _generate_diff("test.py", "same\n", "same\n")
        assert_that(diff).is_equal_to("")


class TestParseFixResponse:
    """Tests for _parse_fix_response."""

    def test_valid_response(self):
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

    def test_invalid_json(self):
        result = _parse_fix_response("not json", "main.py", 10, "B101")
        assert_that(result).is_none()

    def test_identical_code(self):
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

    def test_empty_original(self):
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


class TestGenerateFixes:
    """Tests for generate_fixes function."""

    def test_empty_issues(self, mock_provider):
        result = generate_fixes(
            [],
            mock_provider,
            tool_name="ruff",
        )
        assert_that(result).is_empty()

    def test_generates_fixes_for_unfixable(self, tmp_path):
        # Create a real file
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

    def test_processes_fixable_issues(self, tmp_path):
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

    def test_skips_issues_without_file(self, mock_provider):
        issue = MockIssue(line=1, code="B101", message="test")
        result = generate_fixes(
            [issue],
            mock_provider,
            tool_name="ruff",
        )
        assert_that(result).is_empty()

    def test_respects_max_issues(self, tmp_path):
        source = tmp_path / "test.py"
        source.write_text("x = 1\n" * 50)

        issues = [
            MockIssue(
                file=str(source),
                line=i,
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

    def test_handles_provider_error(self, tmp_path):
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

    def test_skips_issues_with_unreadable_relative_paths(self, tmp_path):
        """Relative paths not resolvable from CWD are silently skipped."""
        # Create file in a subdirectory
        subdir = tmp_path / "tools" / "js"
        subdir.mkdir(parents=True)
        source = subdir / "test.js"
        source.write_text("var x = 1;\n")

        # Issue has path relative to subdir's parent, not to Python CWD
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

        # File can't be read from CWD → no API calls, no suggestions
        assert_that(provider.calls).is_empty()
        assert_that(result).is_empty()

    def test_provider_prompt_uses_workspace_relative_path(self, tmp_path):
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

    def test_skips_issue_outside_workspace_root(self, tmp_path):
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


class TestConcurrentGenerateFixes:
    """Tests for concurrent fix generation via ThreadPoolExecutor."""

    def test_concurrent_generation_with_multiple_workers(self, tmp_path):
        """generate_fixes with max_workers=3 exercises the ThreadPoolExecutor path."""
        source = tmp_path / "test.py"
        source.write_text("line1\nline2\nline3\n")

        issues = [
            MockIssue(
                file=str(source),
                line=i,
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

    def test_mixed_success_and_failure_concurrent(self, tmp_path):
        """Concurrent mode: one success, one failure → 1 suggestion returned."""
        source = tmp_path / "test.py"
        source.write_text("line1\nline2\n")

        issues = [
            MockIssue(
                file=str(source),
                line=i,
                code="B101",
                message=f"Issue {i}",
            )
            for i in range(1, 3)
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


class TestTimeoutPropagation:
    """Tests for timeout flowing through generate_fixes."""

    def test_timeout_reaches_provider(self, tmp_path):
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

    def test_default_timeout_is_60(self, tmp_path):
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


class TestCallProviderRetry:
    """Tests for _call_provider retry behavior via with_retry."""

    def test_retries_on_provider_error(self, tmp_path):
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
        success_response = AIResponse(
            content=json.dumps(
                {
                    "original_code": "x = 1",
                    "suggested_code": "x = 2",
                    "explanation": "Fix",
                    "confidence": "high",
                },
            ),
            model="mock",
            input_tokens=10,
            output_tokens=10,
            cost_estimate=0.001,
            provider="mock",
        )

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

    def test_no_retry_on_auth_error(self, tmp_path):
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

        # Auth errors propagate immediately — only 1 call, no retries
        assert_that(call_count["n"]).is_equal_to(1)
        assert_that(result).is_empty()

    def test_max_retries_zero_means_no_retry(self, tmp_path):
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


class TestToolResultCwd:
    """Tests for ToolResult.cwd field."""

    def test_cwd_defaults_to_none(self):
        result = ToolResult(name="test", success=True)
        assert_that(result.cwd).is_none()

    def test_cwd_preserves_value(self):
        result = ToolResult(name="test", success=True, cwd="/some/path")
        assert_that(result.cwd).is_equal_to("/some/path")


class TestRelativePathResolution:
    """Tests for resolving relative issue paths using ToolResult.cwd."""

    def test_resolves_relative_paths_with_cwd(self, tmp_path):
        """Issues with relative paths should be resolved using result.cwd."""
        # Create file in a subdirectory (simulating tool CWD)
        tool_cwd = tmp_path / "test_samples" / "tools"
        js_dir = tool_cwd / "javascript" / "oxlint"
        js_dir.mkdir(parents=True)
        source = js_dir / "violations.js"
        source.write_text("var x = 1;\n")

        # Issue has relative path (as oxlint would report)
        issue = MockIssue(
            file="javascript/oxlint/violations.js",
            line=1,
            code="no-var",
            message="Unexpected var",
        )

        # Simulate the path resolution from _run_ai_fix_combined
        cwd = str(tool_cwd)
        if not os.path.isabs(issue.file):
            issue.file = os.path.join(cwd, issue.file)

        # Now generate_fixes should be able to read the file
        provider = MockAIProvider()
        generate_fixes(
            [issue],
            provider,
            tool_name="oxlint",
            workspace_root=tmp_path,
        )

        # File is now readable → API call is made
        assert_that(provider.calls).is_length(1)

    def test_absolute_paths_unchanged_by_resolution(self):
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

    def test_no_resolution_when_cwd_is_none(self):
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

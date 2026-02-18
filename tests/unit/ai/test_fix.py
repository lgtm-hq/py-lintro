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

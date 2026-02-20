"""Tests for AI summary service and display."""

from __future__ import annotations

import json

from assertpy import assert_that

from lintro.ai.display import (
    render_summary_github,
    render_summary_markdown,
    render_summary_terminal,
)
from lintro.ai.models import AISummary
from lintro.ai.providers.base import AIResponse
from lintro.ai.summary import (
    _build_issues_digest,
    _parse_summary_response,
    generate_post_fix_summary,
    generate_summary,
)
from lintro.models.core.tool_result import ToolResult
from tests.unit.ai.conftest import MockAIProvider, MockIssue


class TestBuildIssuesDigest:
    """Tests for _build_issues_digest."""

    def test_builds_digest_from_results(self):
        issues = [
            MockIssue(file="src/a.py", line=10, message="Use of assert", code="B101"),
            MockIssue(file="src/b.py", line=20, message="Use of assert", code="B101"),
            MockIssue(file="src/a.py", line=42, message="Line too long", code="E501"),
        ]
        result = ToolResult(
            name="ruff",
            success=True,
            issues_count=3,
            issues=issues,
        )
        digest = _build_issues_digest([result])
        assert_that(digest).contains("ruff")
        assert_that(digest).contains("[B101]")
        assert_that(digest).contains("[E501]")
        assert_that(digest).contains("x2")  # B101 count

    def test_empty_results(self):
        digest = _build_issues_digest([])
        assert_that(digest).is_empty()

    def test_skipped_results_excluded(self):
        result = ToolResult(
            name="ruff",
            success=True,
            issues_count=0,
            skipped=True,
            skip_reason="not installed",
        )
        digest = _build_issues_digest([result])
        assert_that(digest).is_empty()

    def test_sample_locations_capped(self):
        issues = [
            MockIssue(file=f"src/f{i}.py", line=i, message="test", code="E501")
            for i in range(10)
        ]
        result = ToolResult(
            name="ruff",
            success=True,
            issues_count=10,
            issues=issues,
        )
        digest = _build_issues_digest([result])
        # Should show 3 samples + "(+7 more)"
        assert_that(digest).contains("+7 more")

    def test_digest_redacts_absolute_paths_for_provider(self, tmp_path):
        absolute_file = tmp_path / "src" / "hidden.py"
        absolute_file.parent.mkdir(parents=True)
        absolute_file.write_text("x = 1\n", encoding="utf-8")

        result = ToolResult(
            name="ruff",
            success=True,
            issues_count=1,
            issues=[
                MockIssue(
                    file=str(absolute_file),
                    line=1,
                    message="Line too long",
                    code="E501",
                ),
            ],
        )

        digest = _build_issues_digest([result], workspace_root=tmp_path)
        assert_that(digest).contains("src/hidden.py:1")
        assert_that(digest).does_not_contain(str(absolute_file))


class TestParseSummaryResponse:
    """Tests for _parse_summary_response."""

    def test_valid_json(self):
        content = json.dumps(
            {
                "overview": "Code needs work",
                "key_patterns": ["Pattern 1", "Pattern 2"],
                "priority_actions": ["Action 1"],
                "estimated_effort": "30 minutes",
            },
        )
        result = _parse_summary_response(
            content,
            input_tokens=100,
            output_tokens=50,
            cost_estimate=0.01,
        )
        assert_that(result.overview).is_equal_to("Code needs work")
        assert_that(result.key_patterns).is_length(2)
        assert_that(result.priority_actions).is_length(1)
        assert_that(result.estimated_effort).is_equal_to("30 minutes")
        assert_that(result.input_tokens).is_equal_to(100)
        assert_that(result.cost_estimate).is_equal_to(0.01)

    def test_parses_triage_suggestions(self):
        content = json.dumps(
            {
                "overview": "Some issues",
                "key_patterns": [],
                "priority_actions": [],
                "triage_suggestions": [
                    "B101 in tests — assert is idiomatic, add # noqa: B101",
                ],
                "estimated_effort": "5 minutes",
            },
        )
        result = _parse_summary_response(content)
        assert_that(result.triage_suggestions).is_length(1)
        assert_that(result.triage_suggestions[0]).contains("B101")

    def test_missing_triage_defaults_to_empty(self):
        content = json.dumps({"overview": "Clean", "key_patterns": []})
        result = _parse_summary_response(content)
        assert_that(result.triage_suggestions).is_empty()

    def test_invalid_json_fallback(self):
        result = _parse_summary_response("not json at all")
        assert_that(result.overview).contains("not json")
        assert_that(result.key_patterns).is_empty()

    def test_empty_content(self):
        result = _parse_summary_response("")
        assert_that(result.overview).is_equal_to("Summary unavailable")


class TestGenerateSummary:
    """Tests for generate_summary."""

    def test_returns_none_for_no_issues(self):
        provider = MockAIProvider()
        result = ToolResult(name="ruff", success=True, issues_count=0)
        summary = generate_summary([result], provider)
        assert_that(summary).is_none()
        assert_that(provider.calls).is_empty()

    def test_generates_summary(self):
        issues = [
            MockIssue(file="a.py", line=1, message="bad code", code="E501"),
        ]
        result = ToolResult(
            name="ruff",
            success=True,
            issues_count=1,
            issues=issues,
        )
        response = AIResponse(
            content=json.dumps(
                {
                    "overview": "One issue found",
                    "key_patterns": [],
                    "priority_actions": [],
                    "estimated_effort": "5 minutes",
                },
            ),
            model="mock",
            input_tokens=200,
            output_tokens=100,
            cost_estimate=0.005,
            provider="mock",
        )
        provider = MockAIProvider(responses=[response])

        summary = generate_summary([result], provider)
        assert_that(summary).is_not_none()
        assert summary is not None
        assert_that(summary.overview).is_equal_to("One issue found")
        assert_that(provider.calls).is_length(1)

    def test_handles_provider_error(self):
        issues = [
            MockIssue(file="a.py", line=1, message="bad", code="E501"),
        ]
        result = ToolResult(
            name="ruff",
            success=True,
            issues_count=1,
            issues=issues,
        )

        class ErrorProvider(MockAIProvider):
            def complete(self, prompt, **kwargs):
                raise RuntimeError("API down")

        summary = generate_summary([result], ErrorProvider())
        assert_that(summary).is_none()


class TestGeneratePostFixSummary:
    """Tests for generate_post_fix_summary."""

    def test_returns_none_when_all_resolved(self):
        provider = MockAIProvider()
        result = ToolResult(name="ruff", success=True, issues_count=0)

        summary = generate_post_fix_summary(
            applied=5,
            rejected=0,
            remaining_results=[result],
            provider=provider,
        )
        assert_that(summary).is_none()

    def test_generates_post_fix_summary(self):
        issues = [
            MockIssue(file="a.py", line=1, message="remaining", code="B101"),
        ]
        result = ToolResult(
            name="ruff",
            success=True,
            issues_count=1,
            issues=issues,
        )
        response = AIResponse(
            content=json.dumps(
                {
                    "overview": "Fixed 3, 1 remains",
                    "key_patterns": ["Assert usage"],
                    "priority_actions": ["Replace asserts"],
                    "estimated_effort": "10 minutes",
                },
            ),
            model="mock",
            input_tokens=100,
            output_tokens=50,
            cost_estimate=0.002,
            provider="mock",
        )
        provider = MockAIProvider(responses=[response])

        summary = generate_post_fix_summary(
            applied=3,
            rejected=1,
            remaining_results=[result],
            provider=provider,
        )
        assert_that(summary).is_not_none()
        assert summary is not None
        assert_that(summary.overview).contains("Fixed 3")


class TestRenderSummaryTerminal:
    """Tests for render_summary_terminal."""

    def test_renders_overview(self):
        summary = AISummary(
            overview="Code quality is good overall.",
            key_patterns=["Missing docstrings"],
            priority_actions=["Add docstrings to public functions"],
            estimated_effort="15 minutes",
        )
        output = render_summary_terminal(summary)
        assert_that(output).contains("Code quality is good overall")
        assert_that(output).contains("Missing docstrings")
        assert_that(output).contains("Add docstrings")
        assert_that(output).contains("15 minutes")

    def test_empty_summary(self):
        summary = AISummary()
        output = render_summary_terminal(summary)
        assert_that(output).is_empty()

    def test_cost_display(self):
        summary = AISummary(
            overview="Test",
            input_tokens=100,
            output_tokens=50,
            cost_estimate=0.005,
        )
        with_cost = render_summary_terminal(summary, show_cost=True)
        assert_that(with_cost).contains("~150")

        without_cost = render_summary_terminal(summary, show_cost=False)
        assert_that(without_cost).does_not_contain("~150")

    def test_strips_leading_numbers_from_priority_actions(self):
        summary = AISummary(
            overview="Test",
            priority_actions=[
                "1. Fix OpenAI imports",
                "2) Replace asserts",
                "No number prefix here",
            ],
        )
        output = render_summary_terminal(summary)
        # Should not have double numbering like "1. 1. Fix"
        assert_that(output).does_not_contain("1. 1.")
        assert_that(output).does_not_contain("2. 2)")
        # Content should still appear
        assert_that(output).contains("Fix OpenAI imports")
        assert_that(output).contains("Replace asserts")
        assert_that(output).contains("No number prefix here")

    def test_renders_triage_suggestions(self):
        summary = AISummary(
            overview="Issues found",
            triage_suggestions=[
                "B101 in test files — assert is idiomatic, add # noqa: B101",
                "1. E501 in generated code — long lines expected",
            ],
        )
        output = render_summary_terminal(summary)
        assert_that(output).contains("Triage")
        assert_that(output).contains("B101 in test files")
        # Leading number should be stripped
        assert_that(output).contains("E501 in generated code")

    def test_omits_triage_when_empty(self):
        summary = AISummary(overview="Clean code", triage_suggestions=[])
        output = render_summary_terminal(summary)
        assert_that(output).does_not_contain("Triage")


class TestRenderSummaryGitHub:
    """Tests for render_summary_github."""

    def test_renders_with_groups(self):
        summary = AISummary(
            overview="Issues found",
            key_patterns=["Pattern A"],
            priority_actions=["Fix A"],
        )
        output = render_summary_github(summary)
        assert_that(output).contains("::group::")
        assert_that(output).contains("::endgroup::")
        assert_that(output).contains("Issues found")
        assert_that(output).contains("Pattern A")

    def test_strips_leading_numbers_from_priority_actions(self):
        summary = AISummary(
            overview="Test",
            priority_actions=["1. Fix imports", "2. Add tests"],
        )
        output = render_summary_github(summary)
        assert_that(output).does_not_contain("1. 1.")
        assert_that(output).contains("Fix imports")

    def test_renders_triage_suggestions(self):
        summary = AISummary(
            overview="Issues found",
            triage_suggestions=[
                "B101 in test files — assert is idiomatic, add # noqa: B101",
                "E501 in generated code — long lines are expected",
            ],
        )
        output = render_summary_github(summary)
        assert_that(output).contains("Triage")
        assert_that(output).contains("B101 in test files")
        assert_that(output).contains("E501 in generated code")

    def test_omits_triage_when_empty(self):
        summary = AISummary(overview="Clean code", triage_suggestions=[])
        output = render_summary_github(summary)
        assert_that(output).does_not_contain("Triage")


class TestRenderSummaryMarkdown:
    """Tests for render_summary_markdown."""

    def test_renders_with_details(self):
        summary = AISummary(
            overview="Some issues",
            key_patterns=["Pattern X"],
            priority_actions=["Action Y"],
            estimated_effort="1 hour",
        )
        output = render_summary_markdown(summary)
        assert_that(output).contains("<details>")
        assert_that(output).contains("</details>")
        assert_that(output).contains("Some issues")
        assert_that(output).contains("Pattern X")
        assert_that(output).contains("1 hour")

    def test_renders_triage_suggestions(self):
        summary = AISummary(
            overview="Issues found",
            triage_suggestions=[
                "B101 in test files — assert is idiomatic, add # noqa: B101",
            ],
        )
        output = render_summary_markdown(summary)
        assert_that(output).contains("Triage")
        assert_that(output).contains("B101 in test files")

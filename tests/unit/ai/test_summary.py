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

# -- _build_issues_digest -----------------------------------------------------


def test_build_issues_digest_builds_digest_from_results():
    """Verify digest includes tool names, issue codes, and occurrence counts."""
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


def test_build_issues_digest_empty_results():
    """Verify empty results list produces an empty digest string."""
    digest = _build_issues_digest([])
    assert_that(digest).is_empty()


def test_build_issues_digest_skipped_results_excluded():
    """Verify skipped tool results are excluded from the digest."""
    result = ToolResult(
        name="ruff",
        success=True,
        issues_count=0,
        skipped=True,
        skip_reason="not installed",
    )
    digest = _build_issues_digest([result])
    assert_that(digest).is_empty()


def test_build_issues_digest_sample_locations_capped():
    """Verify sample locations are capped at 3 with a count of remaining shown."""
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


def test_build_issues_digest_redacts_absolute_paths_for_provider(tmp_path):
    """Verify absolute file paths are converted to workspace-relative in the digest."""
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


# -- _parse_summary_response --------------------------------------------------


def test_parse_summary_response_valid_json():
    """Verify valid JSON response is parsed into an AISummary with all fields."""
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


def test_parse_summary_response_parses_triage_suggestions():
    """Verify triage_suggestions field is parsed from the JSON response."""
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


def test_parse_summary_response_missing_triage_defaults_to_empty():
    """Verify missing triage_suggestions key defaults to an empty list."""
    content = json.dumps({"overview": "Clean", "key_patterns": []})
    result = _parse_summary_response(content)
    assert_that(result.triage_suggestions).is_empty()


def test_parse_summary_response_invalid_json_fallback():
    """Verify invalid JSON falls back to using raw content as the overview."""
    result = _parse_summary_response("not json at all")
    assert_that(result.overview).contains("not json")
    assert_that(result.key_patterns).is_empty()


def test_parse_summary_response_empty_content():
    """Verify empty content returns a summary with 'Summary unavailable' overview."""
    result = _parse_summary_response("")
    assert_that(result.overview).is_equal_to("Summary unavailable")


def test_parse_summary_response_non_dict_json_list():
    """When json.loads returns a list, the isinstance(data, dict) check triggers."""
    content = json.dumps(["item1", "item2"])
    result = _parse_summary_response(
        content,
        input_tokens=50,
        output_tokens=25,
        cost_estimate=0.003,
    )
    assert_that(result.overview).contains("item1")
    assert_that(result.key_patterns).is_empty()
    assert_that(result.input_tokens).is_equal_to(50)
    assert_that(result.cost_estimate).is_equal_to(0.003)


def test_parse_summary_response_non_dict_json_string():
    """Non-dict JSON triggers the isinstance(data, dict) fallback."""
    content = json.dumps("just a string")
    result = _parse_summary_response(
        content,
        input_tokens=10,
        output_tokens=5,
        cost_estimate=0.001,
    )
    assert_that(result.overview).contains("just a string")
    assert_that(result.key_patterns).is_empty()
    assert_that(result.input_tokens).is_equal_to(10)


def test_parse_summary_response_non_dict_json_int():
    """When json.loads returns an integer, the isinstance(data, dict) check triggers."""
    content = json.dumps(42)
    result = _parse_summary_response(content)
    assert_that(result.overview).contains("42")
    assert_that(result.key_patterns).is_empty()


# -- generate_summary ---------------------------------------------------------


def test_generate_summary_returns_none_for_no_issues():
    """Returns None and skips provider when no issues exist."""
    provider = MockAIProvider()
    result = ToolResult(name="ruff", success=True, issues_count=0)
    summary = generate_summary([result], provider)
    assert_that(summary).is_none()
    assert_that(provider.calls).is_empty()


def test_generate_summary_generates_summary():
    """Verify generate_summary calls the provider and returns a parsed AISummary."""
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


def test_generate_summary_handles_provider_error():
    """Verify generate_summary returns None when the provider raises an error."""
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


# -- generate_post_fix_summary ------------------------------------------------


def test_generate_post_fix_summary_returns_none_when_all_resolved():
    """Verify post-fix summary returns None when all issues are resolved."""
    provider = MockAIProvider()
    result = ToolResult(name="ruff", success=True, issues_count=0)

    summary = generate_post_fix_summary(
        applied=5,
        rejected=0,
        remaining_results=[result],
        provider=provider,
    )
    assert_that(summary).is_none()


def test_generate_post_fix_summary_generates_post_fix_summary():
    """Verify post-fix summary is generated when remaining issues exist."""
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


# -- render_summary_terminal --------------------------------------------------


def test_render_summary_terminal_renders_overview():
    """Verify terminal rendering includes overview, patterns, actions, and effort."""
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


def test_render_summary_terminal_empty_summary():
    """Verify an empty AISummary produces empty terminal output."""
    summary = AISummary()
    output = render_summary_terminal(summary)
    assert_that(output).is_empty()


def test_render_summary_terminal_cost_display():
    """Verify token cost is shown or hidden based on the show_cost flag."""
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


def test_render_summary_terminal_strips_leading_numbers_from_priority_actions():
    """Leading number prefixes are stripped from priority actions."""
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


def test_render_summary_terminal_renders_triage_suggestions():
    """Terminal rendering includes triage with stripped prefixes."""
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


def test_render_summary_terminal_omits_triage_when_empty():
    """Triage section is omitted when suggestions are empty."""
    summary = AISummary(overview="Clean code", triage_suggestions=[])
    output = render_summary_terminal(summary)
    assert_that(output).does_not_contain("Triage")


# -- render_summary_github ----------------------------------------------------


def test_render_summary_github_renders_with_groups():
    """Verify GitHub rendering wraps content in ::group::/::endgroup:: markers."""
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


def test_render_summary_github_strips_leading_numbers_from_priority_actions():
    """Verify GitHub rendering strips leading number prefixes from priority actions."""
    summary = AISummary(
        overview="Test",
        priority_actions=["1. Fix imports", "2. Add tests"],
    )
    output = render_summary_github(summary)
    assert_that(output).does_not_contain("1. 1.")
    assert_that(output).contains("Fix imports")


def test_render_summary_github_renders_triage_suggestions():
    """Verify GitHub rendering includes triage suggestions section."""
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


def test_render_summary_github_omits_triage_when_empty():
    """Triage section omitted from GitHub output when empty."""
    summary = AISummary(overview="Clean code", triage_suggestions=[])
    output = render_summary_github(summary)
    assert_that(output).does_not_contain("Triage")


# -- render_summary_markdown ---------------------------------------------------


def test_render_summary_markdown_renders_with_details():
    """Verify markdown rendering wraps content in HTML details/summary tags."""
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


def test_render_summary_markdown_renders_triage_suggestions():
    """Verify markdown rendering includes triage suggestions section."""
    summary = AISummary(
        overview="Issues found",
        triage_suggestions=[
            "B101 in test files — assert is idiomatic, add # noqa: B101",
        ],
    )
    output = render_summary_markdown(summary)
    assert_that(output).contains("Triage")
    assert_that(output).contains("B101 in test files")

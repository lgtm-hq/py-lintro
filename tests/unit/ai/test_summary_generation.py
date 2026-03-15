"""Tests for AI summary generation.

Covers _build_issues_digest, _parse_summary_response,
generate_summary, and generate_post_fix_summary.
"""

from __future__ import annotations

import json

from assertpy import assert_that

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
    assert_that(summary).is_not_none()
    assert_that(summary.overview).is_equal_to("One issue found")  # type: ignore[union-attr]  # assertpy is_not_none narrows this
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
    assert_that(summary).is_not_none()
    assert_that(summary.overview).contains("Fixed 3")  # type: ignore[union-attr]  # assertpy is_not_none narrows this

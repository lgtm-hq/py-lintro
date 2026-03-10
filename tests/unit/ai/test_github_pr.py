"""Tests for GitHub PR review comment integration (#704)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.ai.integrations.github_pr import (
    GitHubPRReporter,
    _detect_pr_number,
    _format_inline_comment,
    _format_summary_comment,
)
from lintro.ai.models import AIFixSuggestion, AISummary

_TEST_TOKEN = "ghp_test_fixture_token"  # noqa: S105 — not a real token


@pytest.fixture
def test_token() -> str:
    """Provide a fake GitHub token for testing."""
    return _TEST_TOKEN


# -- TestDetectPRNumber: Tests for PR number detection from GITHUB_REF. ------


def test_detects_pr_number_from_ref() -> None:
    """Detect PR number from GITHUB_REF."""
    with patch.dict("os.environ", {"GITHUB_REF": "refs/pull/42/merge"}):
        assert_that(_detect_pr_number()).is_equal_to(42)


def test_returns_none_for_branch_ref() -> None:
    """Return None for branch refs."""
    with patch.dict("os.environ", {"GITHUB_REF": "refs/heads/main"}):
        assert_that(_detect_pr_number()).is_none()


def test_returns_none_for_empty_ref() -> None:
    """Return None for empty GITHUB_REF."""
    with patch.dict("os.environ", {"GITHUB_REF": ""}):
        assert_that(_detect_pr_number()).is_none()


def test_returns_none_for_missing_ref() -> None:
    """Return None when GITHUB_REF is missing."""
    with patch.dict("os.environ", {}, clear=True):
        assert_that(_detect_pr_number()).is_none()


def test_returns_none_for_malformed_ref() -> None:
    """Return None for malformed pull ref."""
    with patch.dict("os.environ", {"GITHUB_REF": "refs/pull//merge"}):
        assert_that(_detect_pr_number()).is_none()


# -- TestGitHubPRReporter: Tests for the GitHubPRReporter class. -------------


def test_is_available_with_all_context(test_token: str) -> None:
    """Report available with token, repo, and PR."""
    reporter = GitHubPRReporter(
        token=test_token,
        repo="owner/repo",
        pr_number=1,
    )
    assert_that(reporter.is_available()).is_true()


def test_is_not_available_without_token() -> None:
    """Report unavailable when token is empty."""
    reporter = GitHubPRReporter(
        token="",
        repo="owner/repo",
        pr_number=1,
    )
    assert_that(reporter.is_available()).is_false()


def test_is_not_available_without_repo(test_token: str) -> None:
    """Report unavailable when repo is empty."""
    reporter = GitHubPRReporter(
        token=test_token,
        repo="",
        pr_number=1,
    )
    assert_that(reporter.is_available()).is_false()


def test_is_not_available_without_pr_number(test_token: str) -> None:
    """Report unavailable when PR number is None."""
    reporter = GitHubPRReporter(
        token=test_token,
        repo="owner/repo",
        pr_number=None,
    )
    assert_that(reporter.is_available()).is_false()


def test_reads_env_vars() -> None:
    """Read token, repo, and PR number from environment."""
    env = {
        "GITHUB_TOKEN": "ghp_from_env",
        "GITHUB_REPOSITORY": "org/repo",
        "GITHUB_REF": "refs/pull/99/merge",
    }
    with patch.dict("os.environ", env, clear=True):
        reporter = GitHubPRReporter()
        assert_that(reporter.token).is_equal_to("ghp_from_env")
        assert_that(reporter.repo).is_equal_to("org/repo")
        assert_that(reporter.pr_number).is_equal_to(99)


def test_post_review_comments_returns_false_when_unavailable() -> None:
    """Return False when reporter is unavailable."""
    reporter = GitHubPRReporter(token="", repo="", pr_number=None)
    result = reporter.post_review_comments([], summary=None)
    assert_that(result).is_false()


def test_post_review_comments_posts_summary(test_token: str) -> None:
    """Post summary as issue comment."""
    reporter = GitHubPRReporter(
        token=test_token,
        repo="owner/repo",
        pr_number=5,
    )
    summary = AISummary(overview="Test overview", key_patterns=["pattern1"])

    with patch.object(reporter, "_post_issue_comment", return_value=True) as mock:
        result = reporter.post_review_comments([], summary=summary)
        assert_that(result).is_true()
        mock.assert_called_once()
        body = mock.call_args[0][0]
        assert_that(body).contains("Test overview")
        assert_that(body).contains("pattern1")


def test_post_review_comments_posts_suggestions(test_token: str) -> None:
    """Post fix suggestions as review comments."""
    reporter = GitHubPRReporter(
        token=test_token,
        repo="owner/repo",
        pr_number=5,
    )
    suggestions = [
        AIFixSuggestion(
            file="src/main.py",
            line=10,
            code="B101",
            tool_name="bandit",
            explanation="Replace assert",
            confidence="high",
        ),
    ]

    with patch.object(reporter, "_post_review", return_value=True) as mock:
        result = reporter.post_review_comments(suggestions)
        assert_that(result).is_true()
        mock.assert_called_once()


def test_api_request_constructs_correct_request(test_token: str) -> None:
    """Construct API request with auth header and JSON body."""
    reporter = GitHubPRReporter(
        token=test_token,
        repo="owner/repo",
        pr_number=5,
    )

    mock_response = MagicMock()
    mock_response.status = 201
    mock_response.__enter__ = MagicMock(return_value=mock_response)
    mock_response.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_response) as mock_open:
        result = reporter._api_request(
            "POST",
            "https://api.github.com/test",
            {"key": "value"},
        )
        assert_that(result).is_true()
        req = mock_open.call_args[0][0]
        assert_that(req.get_header("Authorization")).is_equal_to(
            f"Bearer {test_token}",
        )
        assert_that(json.loads(req.data)).is_equal_to({"key": "value"})


# -- TestFormatSummaryComment: Tests for summary comment formatting. ---------


def test_includes_overview() -> None:
    """Include overview text in summary comment."""
    summary = AISummary(overview="High-level assessment")
    result = _format_summary_comment(summary)
    assert_that(result).contains("High-level assessment")
    assert_that(result).contains("## Lintro AI Summary")


def test_includes_key_patterns() -> None:
    """Include key patterns section in summary."""
    summary = AISummary(
        overview="Overview",
        key_patterns=["Missing types", "No tests"],
    )
    result = _format_summary_comment(summary)
    assert_that(result).contains("### Key Patterns")
    assert_that(result).contains("- Missing types")
    assert_that(result).contains("- No tests")


def test_includes_priority_actions() -> None:
    """Include numbered priority actions in summary."""
    summary = AISummary(
        overview="Overview",
        priority_actions=["Fix imports", "Add tests"],
    )
    result = _format_summary_comment(summary)
    assert_that(result).contains("### Priority Actions")
    assert_that(result).contains("1. Fix imports")
    assert_that(result).contains("2. Add tests")


def test_includes_triage_suggestions() -> None:
    """Include triage suggestions in summary."""
    summary = AISummary(
        overview="Overview",
        triage_suggestions=["Consider suppressing X"],
    )
    result = _format_summary_comment(summary)
    assert_that(result).contains("### Triage")
    assert_that(result).contains("- Consider suppressing X")


def test_includes_effort_estimate() -> None:
    """Include effort estimate in summary."""
    summary = AISummary(
        overview="Overview",
        estimated_effort="2-3 hours",
    )
    result = _format_summary_comment(summary)
    assert_that(result).contains("*Estimated effort: 2-3 hours*")


# -- TestFormatInlineComment: Tests for inline comment formatting. -----------


def test_includes_code_and_tool() -> None:
    """Include rule code and tool name in comment."""
    s = AIFixSuggestion(
        code="B101",
        tool_name="bandit",
        explanation="Replace assert",
    )
    result = _format_inline_comment(s)
    assert_that(result).contains("**B101**")
    assert_that(result).contains("(bandit)")


def test_includes_explanation() -> None:
    """Include explanation text in comment."""
    s = AIFixSuggestion(explanation="Use if/raise instead")
    result = _format_inline_comment(s)
    assert_that(result).contains("Use if/raise instead")


def test_includes_diff() -> None:
    """Include diff block in comment."""
    s = AIFixSuggestion(
        diff="-old\n+new",
        explanation="Fix",
    )
    result = _format_inline_comment(s)
    assert_that(result).contains("```diff")
    assert_that(result).contains("-old\n+new")


def test_includes_suggestion_block() -> None:
    """Include suggestion code block in comment."""
    s = AIFixSuggestion(
        suggested_code="if not x:\n    raise ValueError",
        explanation="Fix",
    )
    result = _format_inline_comment(s)
    assert_that(result).contains("```suggestion")


def test_includes_confidence_and_risk() -> None:
    """Include confidence and risk level in comment."""
    s = AIFixSuggestion(
        confidence="high",
        risk_level="safe-style",
        explanation="Fix",
    )
    result = _format_inline_comment(s)
    assert_that(result).contains("Confidence: high")
    assert_that(result).contains("Risk: safe-style")


def test_sanitizes_backticks_in_diff() -> None:
    """Sanitize triple backticks inside diff content."""
    s = AIFixSuggestion(
        diff="```python\ncode\n```",
        explanation="Fix",
    )
    result = _format_inline_comment(s)
    assert_that(result).does_not_contain("``````")

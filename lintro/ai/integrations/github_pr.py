"""GitHub PR review comment integration for AI findings.

Posts AI summaries and fix suggestions as inline PR review comments
using the GitHub REST API via ``urllib.request``.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from collections.abc import Sequence
from typing import Any

from lintro.ai.models import AIFixSuggestion, AISummary
from lintro.ai.paths import relative_path

logger = logging.getLogger(__name__)


class GitHubPRReporter:
    """Post AI findings as GitHub PR review comments.

    Requires the following environment variables:
    - ``GITHUB_TOKEN``: GitHub API token with ``pull-requests: write``
    - ``GITHUB_REPOSITORY``: Owner/repo (e.g. ``"octocat/hello-world"``)

    The PR number is detected from ``GITHUB_REF`` (``refs/pull/<n>/merge``)
    or can be provided directly.

    Attributes:
        token: GitHub API token.
        repo: GitHub repository in ``owner/repo`` format.
        pr_number: Pull request number.
        api_base: GitHub API base URL.
    """

    def __init__(
        self,
        *,
        token: str | None = None,
        repo: str | None = None,
        pr_number: int | None = None,
        api_base: str = "https://api.github.com",
    ) -> None:
        """Initialize the GitHub PR reporter.

        Args:
            token: GitHub API token. Falls back to ``GITHUB_TOKEN`` env var.
            repo: Repository in ``owner/repo`` format. Falls back to
                ``GITHUB_REPOSITORY`` env var.
            pr_number: PR number. Falls back to parsing ``GITHUB_REF``.
            api_base: GitHub API base URL.
        """
        self.token = token or os.environ.get("GITHUB_TOKEN", "")
        self.repo = repo or os.environ.get("GITHUB_REPOSITORY", "")
        self.pr_number = pr_number or _detect_pr_number()
        self.api_base = api_base.rstrip("/")

    def is_available(self) -> bool:
        """Check whether all required context is present.

        Returns:
            True if token, repo, and PR number are all set.
        """
        return bool(self.token and self.repo and self.pr_number)

    def post_review_comments(
        self,
        suggestions: Sequence[AIFixSuggestion],
        summary: AISummary | None = None,
    ) -> bool:
        """Post AI findings as PR review comments.

        Posts a top-level comment with the AI summary (if present),
        then individual inline review comments for each fix suggestion.

        Args:
            suggestions: AI fix suggestions to post as inline comments.
            summary: Optional AI summary to post as a top-level comment.

        Returns:
            True if all comments were posted successfully.
        """
        if not self.is_available():
            logger.warning(
                "GitHub PR context not available — skipping review comments",
            )
            return False

        success = True

        if summary and summary.overview:
            body = _format_summary_comment(summary)
            if not self._post_issue_comment(body):
                success = False

        if suggestions and not self._post_review(suggestions):
            success = False

        return success

    def _post_review(self, suggestions: Sequence[AIFixSuggestion]) -> bool:
        """Post inline review comments for fix suggestions.

        Args:
            suggestions: Fix suggestions to post.

        Returns:
            True if the review was posted successfully.
        """
        comments: list[dict[str, Any]] = []
        for s in suggestions:
            rel = relative_path(s.file)
            body = _format_inline_comment(s)
            comment: dict[str, Any] = {
                "path": rel,
                "body": body,
            }
            if s.line:
                comment["line"] = s.line
            comments.append(comment)

        if not comments:
            return True

        payload = {
            "event": "COMMENT",
            "body": "Lintro AI review",
            "comments": comments,
        }
        url = f"{self.api_base}/repos/{self.repo}/pulls/{self.pr_number}/reviews"
        return self._api_request("POST", url, payload)

    def _post_issue_comment(self, body: str) -> bool:
        """Post a top-level issue comment on the PR.

        Args:
            body: Comment body in Markdown.

        Returns:
            True if posted successfully.
        """
        url = f"{self.api_base}/repos/{self.repo}" f"/issues/{self.pr_number}/comments"
        return self._api_request("POST", url, {"body": body})

    def _api_request(
        self,
        method: str,
        url: str,
        payload: dict[str, Any],
    ) -> bool:
        """Make an authenticated GitHub API request.

        Args:
            method: HTTP method.
            url: Full API URL.
            payload: JSON payload.

        Returns:
            True if the request succeeded (2xx status).
        """
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(
                req,
                timeout=30,
            ) as resp:  # noqa: S310 — URL is constructed from trusted config, not user input
                status: int = resp.status
                return 200 <= status < 300
        except urllib.error.HTTPError as e:
            logger.warning(
                "GitHub API request failed: %s %s -> %d",
                method,
                url,
                e.code,
            )
            return False
        except urllib.error.URLError as e:
            logger.warning("GitHub API request error: %s", e.reason)
            return False


def _detect_pr_number() -> int | None:
    """Detect PR number from ``GITHUB_REF`` environment variable.

    Expected format: ``refs/pull/<number>/merge``.

    Returns:
        PR number if detected, else None.
    """
    ref = os.environ.get("GITHUB_REF", "")
    if ref.startswith("refs/pull/") and ref.endswith("/merge"):
        try:
            return int(ref.split("/")[2])
        except (IndexError, ValueError):
            return None
    return None


def _format_summary_comment(summary: AISummary) -> str:
    """Format an AI summary as a Markdown PR comment.

    Args:
        summary: AI summary to format.

    Returns:
        Markdown-formatted comment body.
    """
    lines: list[str] = [
        "## Lintro AI Summary",
        "",
        summary.overview,
    ]

    if summary.key_patterns:
        lines.append("")
        lines.append("### Key Patterns")
        for pattern in summary.key_patterns:
            lines.append(f"- {pattern}")

    if summary.priority_actions:
        lines.append("")
        lines.append("### Priority Actions")
        for i, action in enumerate(summary.priority_actions, 1):
            lines.append(f"{i}. {action}")

    if summary.triage_suggestions:
        lines.append("")
        lines.append("### Triage — Consider Suppressing")
        for suggestion in summary.triage_suggestions:
            lines.append(f"- {suggestion}")

    if summary.estimated_effort:
        lines.append("")
        lines.append(f"*Estimated effort: {summary.estimated_effort}*")

    return "\n".join(lines)


def _format_inline_comment(suggestion: AIFixSuggestion) -> str:
    """Format an AI fix suggestion as an inline review comment.

    Args:
        suggestion: Fix suggestion to format.

    Returns:
        Markdown-formatted inline comment body.
    """
    lines: list[str] = []

    code_label = f"**{suggestion.code}**" if suggestion.code else ""
    tool_label = f" ({suggestion.tool_name})" if suggestion.tool_name else ""
    if code_label:
        lines.append(f"{code_label}{tool_label}")
        lines.append("")

    if suggestion.explanation:
        lines.append(suggestion.explanation)
        lines.append("")

    if suggestion.diff:
        sanitized = suggestion.diff.replace("```", "``\u200b`")
        lines.append("```diff")
        lines.append(sanitized)
        lines.append("```")
        lines.append("")

    if suggestion.suggested_code:
        lines.append("```suggestion")
        lines.append(suggestion.suggested_code)
        lines.append("```")
        lines.append("")

    confidence = suggestion.confidence or "medium"
    risk = suggestion.risk_level or "unknown"
    lines.append(f"Confidence: {confidence} | Risk: {risk}")

    return "\n".join(lines)

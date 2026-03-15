"""GitHub PR review comment integration for AI findings.

Posts AI summaries and fix suggestions as inline PR review comments
using the GitHub REST API via ``urllib.request``.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from loguru import logger

from lintro.ai.enums import ConfidenceLevel
from lintro.ai.models import AIFixSuggestion, AISummary
from lintro.ai.paths import relative_path, to_provider_path


class GitHubPRReporter:
    """Post AI findings as GitHub PR review comments.

    Requires the following environment variables:
    - ``GITHUB_TOKEN``: GitHub API token with ``pull-requests: write``
    - ``GITHUB_REPOSITORY``: Owner/repo (e.g. ``"octocat/hello-world"``)

    The PR number is detected from ``GITHUB_REF`` (``refs/pull/<n>/merge``)
    or can be provided directly.
    """

    def __init__(
        self,
        *,
        token: str | None = None,
        repo: str | None = None,
        pr_number: int | None = None,
        api_base: str = "https://api.github.com",
        workspace_root: Path | None = None,
    ) -> None:
        """Initialize the GitHub PR reporter.

        Args:
            token: GitHub API token. Falls back to ``GITHUB_TOKEN`` env var.
            repo: Repository in ``owner/repo`` format. Falls back to
                ``GITHUB_REPOSITORY`` env var.
            pr_number: PR number. Falls back to parsing ``GITHUB_REF``.
            api_base: GitHub API base URL.
            workspace_root: Workspace root for deriving repo-relative paths.
                Falls back to ``GITHUB_WORKSPACE`` env var, then ``None``
                (which uses ``relative_path()`` as fallback).
        """
        self.token = token or os.environ.get("GITHUB_TOKEN", "")
        self.repo = repo or os.environ.get("GITHUB_REPOSITORY", "")
        self.pr_number = pr_number or _detect_pr_number()
        self.api_base = api_base.rstrip("/")

        self.workspace_root: Path | None
        if workspace_root is not None:
            self.workspace_root = workspace_root
        else:
            gh_ws = os.environ.get("GITHUB_WORKSPACE", "")
            self.workspace_root = Path(gh_ws) if gh_ws else None

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
            # Normalize path: strip leading "./" and ensure forward slashes
            raw_path = (
                to_provider_path(s.file, self.workspace_root)
                if self.workspace_root is not None
                else relative_path(s.file)
            )
            rel = raw_path.lstrip("./").replace("\\", "/") if raw_path else ""
            if not rel:
                continue
            body = _format_inline_comment(s)
            comment: dict[str, Any] = {
                "path": rel,
                "body": body,
            }
            if isinstance(s.line, int) and s.line > 0:
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
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https":
            logger.warning("Refusing non-HTTPS URL: {}", url)
            return False

        try:
            with urllib.request.urlopen(  # noqa: S310 — HTTPS-only validated above  # nosemgrep: dynamic-urllib-use-detected  # nosec B310
                req,
                timeout=30,
            ) as resp:
                status: int = resp.status
                return 200 <= status < 300
        except urllib.error.HTTPError as e:
            logger.warning(
                "GitHub API request failed: {} {} -> {}",
                method,
                url,
                e.code,
            )
            return False
        except urllib.error.URLError as e:
            logger.warning("GitHub API request error: {}", e.reason)
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
        sanitized_code = suggestion.suggested_code.replace("```", "``\u200b`")
        lines.append("```suggestion")
        lines.append(sanitized_code)
        lines.append("```")
        lines.append("")

    confidence = suggestion.confidence or ConfidenceLevel.MEDIUM
    risk = suggestion.risk_level or "unknown"
    lines.append(f"Confidence: {confidence} | Risk: {risk}")

    return "\n".join(lines)

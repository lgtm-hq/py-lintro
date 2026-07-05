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
import warnings
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from loguru import logger

from lintro.ai.enums import ConfidenceLevel
from lintro.ai.models import AIFixSuggestion, AISummary
from lintro.ai.paths import OUTSIDE_WORKSPACE_SENTINEL, to_provider_path


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
                Falls back to ``GITHUB_WORKSPACE`` env var, then the
                git repository root via ``git rev-parse``.
        """
        self.token = token if token is not None else os.environ.get("GITHUB_TOKEN", "")
        self.repo = (
            repo if repo is not None else os.environ.get("GITHUB_REPOSITORY", "")
        )
        self.pr_number = pr_number if pr_number is not None else _detect_pr_number()
        self.api_base = api_base.rstrip("/")

        self.workspace_root: Path | None
        if workspace_root is not None:
            self.workspace_root = workspace_root
        else:
            gh_ws = os.environ.get("GITHUB_WORKSPACE", "")
            self.workspace_root = Path(gh_ws) if gh_ws else _detect_repo_root()

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
            if not self.post_issue_comment(body):
                success = False

        if suggestions and not self._post_review(suggestions):
            success = False

        return success

    def _post_review(self, suggestions: Sequence[AIFixSuggestion]) -> bool:
        """Post inline review comments for fix suggestions.

        Suggestions whose file/line can be mapped to the PR diff are posted
        as inline review comments.  Any suggestion that cannot be mapped
        (file not in diff, or line outside changed hunks) is posted as a
        standalone issue comment so one unmappable entry cannot cause a 422
        that rejects the entire review batch.

        Args:
            suggestions: Fix suggestions to post.

        Returns:
            True if all comments were posted successfully.
        """
        diff_lines = self.fetch_pr_diff_lines()
        comments: list[dict[str, Any]] = []
        fallback_suggestions: list[AIFixSuggestion] = []

        for s in suggestions:
            # Resolve repo-relative path
            if self.workspace_root is not None:
                raw_path = to_provider_path(s.file, self.workspace_root)
            else:
                raw_path = s.file
            rel = raw_path.removeprefix("./").replace("\\", "/") if raw_path else ""
            # Skip empty, outside-workspace sentinel, and parent-relative paths.
            # Note: absence of "/" does not imply out-of-workspace — repo-root
            # files like "README.md" or "pyproject.toml" are valid.
            if not rel or rel == OUTSIDE_WORKSPACE_SENTINEL or rel.startswith(".."):
                continue
            body = _format_inline_comment(s)
            has_line = isinstance(s.line, int) and s.line > 0

            # Suggestions without a valid line or not in the PR diff fall back
            # to standalone issue comments instead of inline review comments.
            if (
                not has_line
                or diff_lines is None
                or s.line not in diff_lines.get(rel, set())
            ):
                fallback_suggestions.append(s)
                continue

            comment: dict[str, Any] = {
                "path": rel,
                "body": body,
                "line": s.line,
                "side": "RIGHT",
            }
            comments.append(comment)

        success = True

        if comments:
            payload = {
                "event": "COMMENT",
                "body": "Lintro AI review",
                "comments": comments,
            }
            url = f"{self.api_base}/repos/{self.repo}/pulls/{self.pr_number}/reviews"
            if not self.api_request("POST", url, payload):
                success = False

        # Post unmappable suggestions as standalone issue comments
        for s in fallback_suggestions:
            body = _format_inline_comment(s)
            location = f"`{s.file}:{s.line}`" if s.line else f"`{s.file}`"
            if not self.post_issue_comment(f"{location}\n\n{body}"):
                success = False

        return success

    def fetch_pr_diff_lines(self) -> dict[str, set[int]] | None:
        """Fetch changed lines per file from the PR diff.

        Paginates through all pages of the ``GET /pulls/{pr}/files``
        endpoint (up to 100 files per page) so large PRs are fully covered.

        Returns:
            Mapping of ``{file_path: {line_numbers...}}`` for right-side
            (added/modified) lines, or ``None`` if the diff cannot be fetched.
        """
        base_url = f"{self.api_base}/repos/{self.repo}/pulls/{self.pr_number}/files"
        parsed = urllib.parse.urlparse(base_url)
        if parsed.scheme != "https":
            return None

        all_files: list[dict[str, Any]] = []
        page = 1
        while True:
            url = f"{base_url}?per_page=100&page={page}"
            req = urllib.request.Request(
                url,
                method="GET",
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            try:
                with urllib.request.urlopen(  # noqa: S310 — HTTPS-only validated above  # nosemgrep: dynamic-urllib-use-detected  # nosec B310
                    req,
                    timeout=30,
                ) as resp:
                    files_page = json.loads(resp.read().decode())
            except (urllib.error.URLError, json.JSONDecodeError, OSError):
                logger.debug(
                    "Failed to fetch PR diff; skipping diff-position filtering",
                )
                return None

            if not files_page:
                break
            all_files.extend(files_page)
            if len(files_page) < 100:
                break
            page += 1

        result: dict[str, set[int]] = {}
        for f in all_files:
            filename = f.get("filename", "")
            patch = f.get("patch", "")
            if not filename or not patch:
                continue
            result[filename] = _parse_patch_lines(patch)
        return result

    def post_issue_comment(self, body: str) -> bool:
        """Post a top-level issue comment on the PR.

        Args:
            body: Comment body in Markdown.

        Returns:
            True if posted successfully.
        """
        url = f"{self.api_base}/repos/{self.repo}/issues/{self.pr_number}/comments"
        return self.api_request("POST", url, {"body": body})

    def api_request(
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
            try:
                body = e.read().decode("utf-8", "replace")[:500]
            except (AttributeError, UnicodeDecodeError, ValueError, OSError):
                body = "<unreadable>"
            logger.warning(
                "GitHub API request failed: {} {} -> {}: {}",
                method,
                url,
                e.code,
                body,
            )
            return False
        except urllib.error.URLError as e:
            logger.warning("GitHub API request error: {}", e.reason)
            return False

    def _fetch_pr_diff_lines(self) -> dict[str, set[int]] | None:
        """Deprecated alias for :meth:`fetch_pr_diff_lines`.

        Returns:
            Result of :meth:`fetch_pr_diff_lines`.
        """
        warnings.warn(
            "GitHubPRReporter._fetch_pr_diff_lines is deprecated; "
            "use fetch_pr_diff_lines.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.fetch_pr_diff_lines()

    def _post_issue_comment(self, body: str) -> bool:
        """Deprecated alias for :meth:`post_issue_comment`.

        Args:
            body: Comment body in Markdown.

        Returns:
            Result of :meth:`post_issue_comment`.
        """
        warnings.warn(
            "GitHubPRReporter._post_issue_comment is deprecated; "
            "use post_issue_comment.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.post_issue_comment(body)

    def _api_request(
        self,
        method: str,
        url: str,
        payload: dict[str, Any],
    ) -> bool:
        """Deprecated alias for :meth:`api_request`.

        Args:
            method: HTTP method.
            url: Full API URL.
            payload: JSON payload.

        Returns:
            Result of :meth:`api_request`.
        """
        warnings.warn(
            "GitHubPRReporter._api_request is deprecated; use api_request.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.api_request(method, url, payload)


def _detect_repo_root() -> Path | None:
    """Detect the git repository root via ``git rev-parse``.

    Returns:
        Repository root path, or ``None`` if detection fails.
    """
    import shutil
    import subprocess

    if not shutil.which("git"):
        return None

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        toplevel = result.stdout.strip()
        return Path(toplevel) if toplevel else None
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return None


def _parse_patch_lines(patch: str) -> set[int]:
    """Extract right-side (new) line numbers from a unified diff patch.

    Args:
        patch: The ``patch`` field from the GitHub files API.

    Returns:
        Set of line numbers on the right side of the diff.
    """
    import re

    lines: set[int] = set()
    current_line = 0
    for raw_line in patch.split("\n"):
        hunk_match = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)", raw_line)
        if hunk_match:
            current_line = int(hunk_match.group(1))
            continue
        if raw_line.startswith("-"):
            # Deleted line — doesn't advance right-side counter
            continue
        if raw_line.startswith("+"):
            lines.add(current_line)
        # Both context lines and additions advance the right-side counter
        current_line += 1
    return lines


def _detect_pr_number() -> int | None:
    """Detect PR number from the GitHub event payload or ``GITHUB_REF``.

    Tries ``GITHUB_EVENT_PATH`` first (works for ``pull_request_target``
    workflows), then falls back to parsing ``GITHUB_REF``
    (``refs/pull/<number>/merge``).

    Returns:
        PR number if detected, else None.
    """
    # Try event payload first (covers pull_request_target workflows)
    event_path = os.environ.get("GITHUB_EVENT_PATH", "")
    if event_path:
        try:
            with open(event_path) as f:
                event = json.load(f)
            number = event.get("number")
            if isinstance(number, int) and number > 0:
                return number
        except (OSError, json.JSONDecodeError, TypeError, AttributeError):
            pass

    # Fall back to GITHUB_REF parsing
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
        lines.extend(f"- {pattern}" for pattern in summary.key_patterns)

    if summary.priority_actions:
        lines.append("")
        lines.append("### Priority Actions")
        lines.extend(
            f"{i}. {action}" for i, action in enumerate(summary.priority_actions, 1)
        )

    if summary.triage_suggestions:
        lines.append("")
        lines.append("### Triage — Consider Suppressing")
        lines.extend(f"- {suggestion}" for suggestion in summary.triage_suggestions)

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

"""GitHub PR review comment integration for AI findings.

Posts AI summaries and fix suggestions as inline PR review comments
using the GitHub REST API via ``urllib.request``.
"""

from __future__ import annotations

import json
import os
import re
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
from lintro.ai.review.models.review_thread import ReviewThread

#: Lists every review thread with its root comment's REST id, so a stored
#: comment id can be joined to the thread node id the mutation requires.
_REVIEW_THREADS_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          isResolved
          comments(first: 1) { nodes { databaseId } }
        }
      }
    }
  }
}
"""

#: Marks a thread resolved. There is deliberately no unresolve counterpart:
#: a regression opens a new thread rather than reopening a settled one (#1912).
_RESOLVE_THREAD_MUTATION = """
mutation($threadId: ID!) {
  resolveReviewThread(input: { threadId: $threadId }) {
    thread { isResolved }
  }
}
"""

#: Commit shas are interpolated into a compare URL, so only hex refs are
#: accepted — a branch name or user-supplied ref must not reach path building.
#: Matched with ``fullmatch``: ``$`` alone would admit a trailing newline, and
#: a control character in the URL makes ``Request`` raise outside the handler.
_SHA_RE = re.compile(r"[0-9a-fA-F]{4,40}")


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

    def _authorized_request(
        self,
        *,
        url: str,
        method: str,
        data: bytes | None = None,
        content_type: str = "",
    ) -> urllib.request.Request:
        """Build an API request whose token cannot leak to a redirect target.

        ``urllib`` copies ordinary headers onto redirected requests without
        re-checking the scheme or the host, so a ``302`` to ``http://…`` would
        replay the ``Authorization`` header in cleartext to an arbitrary origin
        (CWE-319). Adding it as an *unredirected* header is urllib's mechanism
        for exactly this: the token is sent to the URL validated here and to no
        other. A redirected GitHub endpoint (a renamed repository, say) then
        answers 401 and the caller degrades — the token stays put either way.

        Args:
            url: Fully-built request URL. The caller validates its scheme.
            method: HTTP method.
            data: Optional request body.
            content_type: Optional ``Content-Type`` for a request with a body.

        Returns:
            The prepared request.
        """
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if content_type:
            headers["Content-Type"] = content_type
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        req.add_unredirected_header("Authorization", f"Bearer {self.token}")
        return req

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
            req = self._authorized_request(url=url, method="GET")
            try:
                with urllib.request.urlopen(  # noqa: S310 — HTTPS-only validated above  # nosemgrep: dynamic-urllib-use-detected — HTTPS-only validated above  # nosec B310 — HTTPS-only validated above
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

        return _files_to_lines(files=all_files)

    def fetch_compare_lines(
        self,
        *,
        base: str,
        head: str,
    ) -> dict[str, set[int]] | None:
        """Fetch the changed lines between two commits.

        Used to establish *this round's* posted diff (#1911): a committable
        ``suggestion`` block is only valid on lines the round actually pushed,
        which is a strictly smaller set than the PR's cumulative diff.

        Args:
            base: Base commit sha (the previously reviewed head).
            head: Head commit sha for this round.

        Returns:
            Mapping of ``{file_path: {line_numbers...}}`` for right-side lines,
            or ``None`` when the shas are unusable or the comparison cannot be
            fetched. ``None`` is a refusal, not an empty diff: callers must not
            read it as "nothing changed".

            Deliberately a single unpaginated request. Unlike the PR *files*
            endpoint, the compare endpoint paginates its ``commits`` array, not
            its ``files`` array, which GitHub caps server-side at 300 entries.
            Walking ``page`` here would re-request commits and tell us nothing
            new about files. A comparison wider than that cap simply yields
            fewer committable suggestions, which is the safe direction: those
            findings fall back to a described fix.
        """
        if not _SHA_RE.fullmatch(base) or not _SHA_RE.fullmatch(head):
            logger.debug("Refusing to compare non-sha refs: {}...{}", base, head)
            return None
        url = f"{self.api_base}/repos/{self.repo}/compare/{base}...{head}"
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https":
            return None

        req = self._authorized_request(url=url, method="GET")
        try:
            with urllib.request.urlopen(  # noqa: S310 — HTTPS-only validated above  # nosemgrep: dynamic-urllib-use-detected — HTTPS-only validated above  # nosec B310 — HTTPS-only validated above
                req,
                timeout=30,
            ) as resp:
                payload = json.loads(resp.read().decode())
        except (urllib.error.URLError, json.JSONDecodeError, OSError):
            logger.debug(
                "Failed to compare {}...{}; treating this round's diff as unknown",
                base,
                head,
            )
            return None

        files = payload.get("files") if isinstance(payload, dict) else None
        if not isinstance(files, list):
            return None
        return _files_to_lines(files=files)

    def fetch_pr_commit_shas(self) -> list[str] | None:
        """Fetch the PR's commit shas, oldest first.

        Used to state how many commits arrived since the previous review round.
        Failures return ``None`` so callers can omit the count rather than
        report a fabricated one.

        Returns:
            Commit shas in chronological order, or ``None`` when the listing
            could not be fetched.
        """
        base_url = f"{self.api_base}/repos/{self.repo}/pulls/{self.pr_number}/commits"
        parsed = urllib.parse.urlparse(base_url)
        if parsed.scheme != "https":
            return None

        shas: list[str] = []
        page = 1
        while True:
            url = f"{base_url}?per_page=100&page={page}"
            req = self._authorized_request(url=url, method="GET")
            try:
                with urllib.request.urlopen(  # noqa: S310 — HTTPS-only validated above  # nosemgrep: dynamic-urllib-use-detected — HTTPS-only validated above  # nosec B310 — HTTPS-only validated above
                    req,
                    timeout=30,
                ) as resp:
                    commits_page = json.loads(resp.read().decode())
            except (urllib.error.URLError, json.JSONDecodeError, OSError):
                logger.debug("Failed to fetch PR commits; omitting commit count")
                return None

            if not isinstance(commits_page, list) or not commits_page:
                break
            shas.extend(
                str(commit.get("sha", ""))
                for commit in commits_page
                if isinstance(commit, dict) and commit.get("sha")
            )
            if len(commits_page) < 100:
                break
            page += 1
        return shas

    def find_issue_comment(self, *, marker: str) -> tuple[int, str] | None:
        """Find an existing issue comment containing a hidden marker.

        Paginates through the PR's issue comments and returns the first one
        whose body contains ``marker`` (an HTML comment used to identify a
        sticky comment maintained across runs). Matching is by marker only —
        not by author — so the first ``lintro-review[bot]`` run can find an
        existing ``github-actions[bot]`` sticky (#2050). GitHub forbids editing
        another actor's comment; ``_upsert_sticky`` then deletes and recreates.

        Args:
            marker: Substring to search for (e.g. ``<!-- lintro-ai-review -->``).

        Returns:
            Tuple of ``(comment_id, body)`` for the matching comment, or
            ``None`` when no match is found or the listing fails.
        """
        base_url = f"{self.api_base}/repos/{self.repo}/issues/{self.pr_number}/comments"
        parsed = urllib.parse.urlparse(base_url)
        if parsed.scheme != "https":
            return None

        page = 1
        while True:
            url = f"{base_url}?per_page=100&page={page}"
            req = self._authorized_request(url=url, method="GET")
            try:
                with urllib.request.urlopen(  # noqa: S310 — HTTPS-only validated above  # nosemgrep: dynamic-urllib-use-detected — HTTPS-only validated above  # nosec B310 — HTTPS-only validated above
                    req,
                    timeout=30,
                ) as resp:
                    comments_page = json.loads(resp.read().decode())
            except (urllib.error.URLError, json.JSONDecodeError, OSError):
                logger.debug("Failed to list PR comments; cannot find sticky comment")
                return None

            if not comments_page:
                return None
            for comment in comments_page:
                body = comment.get("body", "")
                comment_id = comment.get("id")
                if isinstance(comment_id, int) and marker in body:
                    return comment_id, body
            if len(comments_page) < 100:
                return None
            page += 1

    def fetch_review_comments(self) -> list[dict[str, Any]] | None:
        """Fetch the PR's inline review comments, oldest first.

        Used to recover the comment id of a freshly posted inline finding: the
        review-submission endpoint answers with the review, not with the
        comments it created, so the ids are only discoverable by listing.

        Returns:
            The raw comment mappings, or ``None`` when the listing failed.
            ``None`` is a refusal, not an empty PR: a caller must not read it
            as "this PR has no inline comments".
        """
        base_url = f"{self.api_base}/repos/{self.repo}/pulls/{self.pr_number}/comments"
        parsed = urllib.parse.urlparse(base_url)
        if parsed.scheme != "https":
            return None

        comments: list[dict[str, Any]] = []
        page = 1
        while True:
            url = f"{base_url}?per_page=100&page={page}"
            req = self._authorized_request(url=url, method="GET")
            try:
                with urllib.request.urlopen(  # noqa: S310 — HTTPS-only validated above  # nosemgrep: dynamic-urllib-use-detected — HTTPS-only validated above  # nosec B310 — HTTPS-only validated above
                    req,
                    timeout=30,
                ) as resp:
                    page_items = json.loads(resp.read().decode())
            except (urllib.error.URLError, json.JSONDecodeError, OSError):
                logger.debug("Failed to list PR review comments")
                return None

            if not isinstance(page_items, list) or not page_items:
                break
            comments.extend(item for item in page_items if isinstance(item, dict))
            if len(page_items) < 100:
                break
            page += 1
        return comments

    def update_review_comment(self, *, comment_id: int, body: str) -> bool:
        """Edit an existing inline review comment in place.

        Args:
            comment_id: Numeric id of the review comment to edit.
            body: New Markdown body.

        Returns:
            True if the update succeeded.
        """
        url = f"{self.api_base}/repos/{self.repo}/pulls/comments/{comment_id}"
        return self.api_request("PATCH", url, {"body": body})

    @property
    def graphql_url(self) -> str:
        """Return the GraphQL endpoint matching this reporter's REST base.

        Returns:
            ``https://api.github.com/graphql`` for github.com; the sibling
            ``/api/graphql`` endpoint for a GitHub Enterprise ``/api/v3`` base.
        """
        if self.api_base.endswith("/api/v3"):
            return f"{self.api_base.removesuffix('/v3')}/graphql"
        return f"{self.api_base}/graphql"

    def graphql_request(
        self,
        *,
        query: str,
        variables: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Execute a GraphQL query or mutation against the GitHub API.

        Args:
            query: GraphQL document to execute.
            variables: Variable bindings for the document.

        Returns:
            The ``data`` object of a successful response, or ``None`` when the
            request failed, returned unparsable JSON, or carried GraphQL
            ``errors`` — GraphQL answers 200 for a failed mutation, so the
            error array is the only signal that it did not take effect.
        """
        url = self.graphql_url
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https":
            logger.warning("Refusing non-HTTPS GraphQL URL: {}", url)
            return None

        req = self._authorized_request(
            url=url,
            method="POST",
            data=json.dumps({"query": query, "variables": variables}).encode(),
            content_type="application/json",
        )
        try:
            with urllib.request.urlopen(  # noqa: S310 — HTTPS-only validated above  # nosemgrep: dynamic-urllib-use-detected — HTTPS-only validated above  # nosec B310 — HTTPS-only validated above
                req,
                timeout=30,
            ) as resp:
                payload = json.loads(resp.read().decode())
        except (urllib.error.URLError, json.JSONDecodeError, OSError) as exc:
            logger.debug("GitHub GraphQL request failed: {}", exc)
            return None

        if not isinstance(payload, dict):
            return None
        if payload.get("errors"):
            logger.warning(
                "GitHub GraphQL request returned errors: {}",
                payload["errors"],
            )
            return None
        data = payload.get("data")
        return data if isinstance(data, dict) else None

    def fetch_review_threads(self) -> dict[int, ReviewThread] | None:
        """Map each review thread's root comment id to the thread itself.

        ``resolveReviewThread`` takes a thread node id, and the state blob only
        stores REST comment ids, so the two must be joined. The join key is the
        thread's *first* comment: that is the comment lintro posted to open the
        thread, and it is the id persisted for the finding.

        Returns:
            Mapping of root comment database id to thread, or ``None`` when the
            query failed — the caller then skips resolution rather than
            guessing at thread identity.
        """
        if not self.repo or "/" not in self.repo:
            return None
        owner, _, name = self.repo.partition("/")

        threads: dict[int, ReviewThread] = {}
        cursor: str | None = None
        while True:
            data = self.graphql_request(
                query=_REVIEW_THREADS_QUERY,
                variables={
                    "owner": owner,
                    "name": name,
                    "number": self.pr_number,
                    "cursor": cursor,
                },
            )
            if data is None:
                return None
            container = _dig(data, "repository", "pullRequest", "reviewThreads")
            if container is None:
                return None
            for node in container.get("nodes") or []:
                if not isinstance(node, dict):
                    continue
                node_id = node.get("id")
                comments = (node.get("comments") or {}).get("nodes") or []
                root = comments[0] if comments and isinstance(comments[0], dict) else {}
                database_id = root.get("databaseId")
                if isinstance(node_id, str) and isinstance(database_id, int):
                    threads[database_id] = ReviewThread(
                        node_id=node_id,
                        is_resolved=bool(node.get("isResolved")),
                    )
            page_info = container.get("pageInfo") or {}
            if not page_info.get("hasNextPage"):
                return threads
            next_cursor = page_info.get("endCursor")
            if not isinstance(next_cursor, str) or next_cursor == cursor:
                # A server that repeats its cursor would loop forever; stop with
                # what has been collected instead.
                return threads
            cursor = next_cursor

    def resolve_review_thread(self, *, thread_id: str) -> bool:
        """Resolve a PR review thread via the GraphQL mutation.

        Args:
            thread_id: GraphQL node id of the thread to resolve.

        Returns:
            True when GitHub reports the thread as resolved.
        """
        data = self.graphql_request(
            query=_RESOLVE_THREAD_MUTATION,
            variables={"threadId": thread_id},
        )
        if data is None:
            return False
        thread = _dig(data, "resolveReviewThread", "thread")
        return bool(thread and thread.get("isResolved"))

    def update_issue_comment_status(
        self,
        *,
        comment_id: int,
        body: str,
    ) -> int | None:
        """PATCH an issue comment and return the HTTP status.

        Args:
            comment_id: Numeric id of the comment to edit.
            body: New Markdown body.

        Returns:
            The HTTP status, or ``None`` when the request did not complete.
            ``403`` is the actor-mismatch GitHub returns when this token did
            not create the comment.
        """
        url = f"{self.api_base}/repos/{self.repo}/issues/comments/{comment_id}"
        return self.api_http_status(
            method="PATCH",
            url=url,
            payload={"body": body},
        )

    def update_issue_comment(self, *, comment_id: int, body: str) -> bool:
        """Update an existing issue comment in place.

        Args:
            comment_id: Numeric id of the comment to edit.
            body: New Markdown body.

        Returns:
            True if the update succeeded.
        """
        status = self.update_issue_comment_status(
            comment_id=comment_id,
            body=body,
        )
        return status is not None and 200 <= status < 300

    def delete_issue_comment(self, *, comment_id: int) -> bool:
        """Delete an issue comment by id.

        Used to supersede a sticky that this token cannot edit (GitHub binds
        PATCH to the creating actor). Write access can still delete it.

        Args:
            comment_id: Numeric id of the comment to delete.

        Returns:
            True if the deletion succeeded.
        """
        url = f"{self.api_base}/repos/{self.repo}/issues/comments/{comment_id}"
        return self.api_request(method="DELETE", url=url)

    def post_issue_comment(self, body: str) -> bool:
        """Post a top-level issue comment on the PR.

        Args:
            body: Comment body in Markdown.

        Returns:
            True if posted successfully.
        """
        url = f"{self.api_base}/repos/{self.repo}/issues/{self.pr_number}/comments"
        return self.api_request(method="POST", url=url, payload={"body": body})

    def api_http_status(
        self,
        method: str,
        url: str,
        payload: dict[str, Any] | None = None,
    ) -> int | None:
        """Make an authenticated GitHub API request and return the status.

        Args:
            method: HTTP method.
            url: Full API URL.
            payload: JSON payload, or ``None`` for methods with no body.

        Returns:
            The HTTP status when GitHub answered, or ``None`` when the
            request was refused locally or failed in transit.
        """
        data = None if payload is None else json.dumps(payload).encode()
        req = self._authorized_request(
            url=url,
            method=method,
            data=data,
            content_type="application/json" if data is not None else "",
        )
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https":
            logger.warning("Refusing non-HTTPS URL: {}", url)
            return None

        try:
            with urllib.request.urlopen(  # noqa: S310 — HTTPS-only validated above  # nosemgrep: dynamic-urllib-use-detected — HTTPS-only validated above  # nosec B310 — HTTPS-only validated above
                req,
                timeout=30,
            ) as resp:
                return int(resp.status)
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
            return int(e.code)
        except urllib.error.URLError as e:
            logger.warning("GitHub API request error: {}", e.reason)
            return None

    def api_request(
        self,
        method: str,
        url: str,
        payload: dict[str, Any] | None = None,
    ) -> bool:
        """Make an authenticated GitHub API request.

        Args:
            method: HTTP method.
            url: Full API URL.
            payload: JSON payload, or ``None`` for methods with no body.

        Returns:
            True if the request succeeded (2xx status).
        """
        status = self.api_http_status(method=method, url=url, payload=payload)
        return status is not None and 200 <= status < 300

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


def _dig(payload: dict[str, Any], *keys: str) -> dict[str, Any] | None:
    """Walk a chain of mapping keys in an untrusted GraphQL response.

    Args:
        payload: Decoded response object.
        *keys: Successive keys to follow.

    Returns:
        The nested mapping, or ``None`` as soon as a level is missing or is not
        a mapping — a partial GraphQL response must degrade, not raise.
    """
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current if isinstance(current, dict) else None


def _detect_repo_root() -> Path | None:
    """Detect the git repository root via ``git rev-parse``.

    Returns:
        Repository root path, or ``None`` if detection fails.
    """
    import shutil
    import subprocess  # nosec B404 - subprocess is the core mechanism for invoking external tools; all invocations use shell=False

    if not shutil.which("git"):
        return None

    try:
        result = subprocess.run(  # nosec B603 B607 - argv is an internally-built list run with shell=False; binary name resolved from PATH, not attacker-controlled; binary resolved from a known command, no user shell input
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


def _files_to_lines(*, files: Sequence[dict[str, Any]]) -> dict[str, set[int]]:
    """Reduce a GitHub files listing to right-side changed lines per path.

    Args:
        files: Entries from a ``files`` array (PR files or a comparison).

    Returns:
        Mapping of file path to the right-side line numbers it changed. Entries
        that are not mappings are skipped rather than raising — the caller's
        error handling does not wrap this reduction. Entries without a path or
        without a patch (binary files, or files too large for GitHub to render)
        are omitted. A filename appearing on more than one page has its line
        sets merged, not overwritten.
    """
    result: dict[str, set[int]] = {}
    for entry in files:
        if not isinstance(entry, dict):
            continue
        filename = entry.get("filename", "")
        patch = entry.get("patch", "")
        # Type, not merely truthiness: a list ``filename`` is unhashable and a
        # non-string ``patch`` has no ``split``. Either would raise from outside
        # the caller's handler instead of degrading to a described fix.
        if not isinstance(filename, str) or not isinstance(patch, str):
            continue
        if not filename or not patch:
            continue
        result.setdefault(filename, set()).update(_parse_patch_lines(patch))
    return result


def _parse_patch_lines(patch: str) -> set[int]:
    """Extract right-side (new) line numbers from a unified diff patch.

    Args:
        patch: The ``patch`` field from the GitHub files API.

    Returns:
        Set of line numbers on the right side of the diff.
    """
    lines: set[int] = set()
    current_line = 0
    for raw_line in patch.split("\n"):
        hunk_match = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)", raw_line)
        if hunk_match:
            current_line = int(hunk_match.group(1))
            continue
        if raw_line.startswith("\\"):
            # "\ No newline at end of file" marker — not a real line, so it
            # must not advance the right-side counter.
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

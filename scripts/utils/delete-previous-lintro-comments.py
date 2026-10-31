#!/usr/bin/env python3
"""Delete previous lintro bot comments from a GitHub pull request.

This script deletes all comments containing the marker '<!-- lintro-report -->'
from a specified pull request.

Uses httpx for HTTP requests.

Environment Variables:
    GITHUB_TOKEN (str): GitHub API token with repo permissions.
    GITHUB_REPOSITORY (str): Repository in 'owner/repo' format.
    PR_NUMBER (str or int): Pull request number.

Usage:
    uv run python scripts/delete-previous-lintro-comments.py

Intended for use in CI workflows to keep PRs clean of duplicate bot comments.
"""

import os
import sys
from time import sleep

import httpx


def get_marker() -> str:
    """Get the marker/tag from command-line arguments or use default.

    Returns:
        str: The marker/tag to use.
    """
    if len(sys.argv) > 1:
        return sys.argv[1]
    return "<!-- lintro-report -->"


def get_env_var(name: str) -> str:
    """Get an environment variable or exit with error.

    Args:
        name: Name of the environment variable.

    Returns:
        str: Value of the environment variable.
    """
    value: str | None = os.environ.get(name)
    if not value:
        print(f"Error: Environment variable {name} is required.", file=sys.stderr)
        sys.exit(1)
    return value


def get_pr_comments(
    repo: str,
    pr_number: str,
    token: str,
) -> list[dict[str, object]]:
    """Fetch all comments for a pull request with pagination and retries.

    Args:
        repo: Repository in 'owner/repo' format.
        pr_number: Pull request number.
        token: GitHub API token.

    Returns:
        list[dict[str, str | int]]: List of comment objects.

    Raises:
        Exception: If the GitHub API request fails after retries or returns
            a non-successful response status.
        RuntimeError: If the retry loop ends without a successful response.
    """
    base_url: str = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip(
        "/",
    )
    headers: dict[str, str] = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    all_comments: list[dict[str, object]] = []
    page: int = 1
    per_page: int = 100
    with httpx.Client(timeout=15) as client:
        while True:
            url: str = (
                f"{base_url}/repos/{repo}/issues/{pr_number}/comments"
                f"?per_page={per_page}&page={page}"
            )
            # Simple retry/backoff loop
            for attempt in range(3):
                try:
                    response: httpx.Response = client.get(url=url, headers=headers)
                    response.raise_for_status()
                    break
                except Exception:
                    if attempt == 2:
                        raise
                    sleep(0.5 * (2**attempt))
            else:
                raise RuntimeError("Comment request exhausted its retries")
            data = response.json()
            if not isinstance(data, list) or not data:
                break
            all_comments.extend(data)
            if len(data) < per_page:
                break
            page += 1
    return all_comments


def delete_comment(repo: str, comment_id: int, token: str) -> None:
    """Delete a comment by ID.

    Args:
        repo: Repository in 'owner/repo' format.
        comment_id: Comment ID.
        token: GitHub API token.
    """
    base_url: str = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip(
        "/",
    )
    headers: dict[str, str] = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    url: str = f"{base_url}/repos/{repo}/issues/comments/{comment_id}"
    with httpx.Client(timeout=15) as client:
        response: httpx.Response = client.delete(url=url, headers=headers)
        if response.status_code == 204:
            print(f"Deleted comment {comment_id}")
        else:
            print(
                f"Failed to delete comment {comment_id}: "
                f"{response.status_code} {response.text}",
                file=sys.stderr,
            )


def main() -> None:
    """Main entry point for the script."""
    repo: str = get_env_var(name="GITHUB_REPOSITORY")
    pr_number: str = get_env_var(name="PR_NUMBER")
    token: str = get_env_var(name="GITHUB_TOKEN")
    marker: str = get_marker()

    try:
        comments: list[dict[str, object]] = get_pr_comments(
            repo=repo,
            pr_number=pr_number,
            token=token,
        )
    except Exception as e:
        print(f"Error fetching comments: {e}", file=sys.stderr)
        sys.exit(1)

    deleted_any: bool = False
    for comment in comments:
        body = comment.get("body")
        comment_id = comment.get("id")
        if isinstance(body, str) and marker in body and isinstance(comment_id, int):
            delete_comment(repo=repo, comment_id=comment_id, token=token)
            deleted_any = True

    if not deleted_any:
        print(f"No previous comments found to delete for marker: {marker}")


if __name__ == "__main__":
    main()

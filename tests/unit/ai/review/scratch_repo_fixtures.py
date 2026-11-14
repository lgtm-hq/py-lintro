"""Scratch git repositories and doubles shared by the review round tests.

The #3403 and #3413 tests drive real git repositories through ``run_review``
with a canned provider and a fake pull-request thread host; the helpers they
share live here rather than being imported from one another's test modules.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess  # nosec B404 - scratch repositories are driven with fixed git argv
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from lintro.ai.prompts.review import REVIEW_MOVED_VERIFICATION_SYSTEM_PROMPT
from lintro.ai.providers.capabilities import ProviderCapabilities
from lintro.ai.providers.response import AIResponse
from lintro.ai.review.models.review_thread import ReviewThread
from lintro.ai.review.moved_verification import MAX_MOVED_FINDINGS

__all__ = [
    "ThreadHost",
    "commit",
    "fake_provider",
    "git",
    "lines",
    "moved_answer",
    "routed_calls",
]

_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
    "GIT_CONFIG_GLOBAL": os.devnull,
}


def git(cwd: Path, *args: str) -> str:
    """Run git in a scratch repository.

    Args:
        cwd: The repository.
        *args: Git arguments.

    Returns:
        Stripped stdout.
    """
    return subprocess.run(  # nosec B603 B607 - fixed argv over a scratch repository
        [shutil.which("git") or "git", *args],
        cwd=cwd,
        env={**os.environ, **_ENV},
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def lines(count: int, *, label: str) -> str:
    """Return ``count`` numbered filler lines.

    Args:
        count: How many lines.
        label: Prefix that keeps each file's text distinct.

    Returns:
        The text, newline-terminated.
    """
    return "".join(f"{label}_{index} = {index}\n" for index in range(1, count + 1))


def commit(repo: Path, message: str) -> str:
    """Commit every change and return the new head.

    Args:
        repo: The repository.
        message: The commit message.

    Returns:
        The new head.
    """
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


class ThreadHost:
    """A pull request's inline comments and threads, edited in place.

    Attributes:
        bodies: Comment id to current body.
        resolved: Thread node ids resolved so far.
    """

    bodies: dict[int, str]
    resolved: list[str]

    def __init__(self, *, bodies: dict[int, str]) -> None:
        """Hold the comments.

        Args:
            bodies: Comment id to body.
        """
        self.bodies = dict(bodies)
        self.resolved = []

    def update_review_comment(self, *, comment_id: int, body: str) -> bool:
        """Replace a comment's body.

        Args:
            comment_id: The comment.
            body: Its new body.

        Returns:
            True: the edit always takes.
        """
        self.bodies[comment_id] = body
        return True

    def fetch_review_threads(self) -> dict[int, ReviewThread]:
        """Return one open thread per comment.

        Returns:
            Comment id to thread.
        """
        return {
            comment_id: ReviewThread(node_id=f"T{comment_id}")
            for comment_id in self.bodies
        }

    def resolve_review_thread(self, *, thread_id: str) -> bool:
        """Resolve a thread.

        Args:
            thread_id: The thread's node id.

        Returns:
            True: the resolution always takes.
        """
        self.resolved.append(thread_id)
        return True


def fake_provider() -> MagicMock:
    """Return a session-less provider whose complete() is unused."""
    provider = MagicMock()
    # The run session closes every provider it owns (#2302), so the
    # double has to model an awaitable ``aclose``.
    provider.aclose = AsyncMock()
    provider.model_name = "fake-model"
    provider.name = "fake"
    provider.capabilities = ProviderCapabilities(supports_sessions=False)
    return provider


def moved_answer(*, still_present: str, file: str = "", line: int = 0) -> AIResponse:
    """The moved-finding check's answer, the same for every finding (#3466).

    Args:
        still_present: ``"true"``, ``"false"`` or ``"unknown"``.
        file: The location a ``"true"`` answer names, or empty.
        line: Its line, or 0.

    Returns:
        A canned response covering every position the check may ask about.
    """
    entries = [
        {"index": index, "still_present": still_present, "file": file, "line": line}
        for index in range(1, MAX_MOVED_FINDINGS + 1)
    ]
    return AIResponse(
        content=json.dumps({"moved_findings": entries}),
        model="fake-model",
        provider="fake",
        input_tokens=7,
        output_tokens=3,
        cost_estimate=0.002,
    )


def routed_calls(
    *,
    review: AIResponse,
    moved: AIResponse | Exception | None,
    seen: list[str] | None = None,
) -> Callable[..., AIResponse]:
    """Serve the review calls and the moved-finding check separately.

    Args:
        review: The answer to every review call.
        moved: The moved-finding check's answer, an exception it raises, or
            ``None`` to give it the review's answer (an off-schema reply).
        seen: Receives the user prompt of each moved-finding check.

    Returns:
        A ``side_effect`` for ``provider_call.call_ai``.
    """

    def _call(**kwargs: Any) -> AIResponse:
        if kwargs.get("system_prompt") != REVIEW_MOVED_VERIFICATION_SYSTEM_PROMPT:
            return review
        if seen is not None:
            seen.append(str(kwargs.get("user_prompt", "")))
        if isinstance(moved, Exception):
            raise moved
        return review if moved is None else moved

    return _call

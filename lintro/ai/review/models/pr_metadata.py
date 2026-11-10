"""Pull request metadata for review context."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PRMetadata:
    """Pull request metadata fetched via the GitHub CLI.

    Attributes:
        title: PR title.
        body: PR description body.
        number: PR number.
        repo: Repository in ``owner/name`` format for PR metadata and gh calls.
        changed_files: Number of changed files reported by GitHub.
        head_repo: Head repository in ``owner/name`` format when it differs from
            ``repo`` (for example fork pull requests).
    """

    title: str
    body: str
    number: int
    repo: str
    changed_files: int = 0
    head_repo: str | None = None

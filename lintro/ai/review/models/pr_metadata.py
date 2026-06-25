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
        repo: Repository in ``owner/name`` format.
    """

    title: str
    body: str
    number: int
    repo: str

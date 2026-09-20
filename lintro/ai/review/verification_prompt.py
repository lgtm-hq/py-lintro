"""Prompt rendering for the verification pass (#2728).

Each selected finding is rendered with its cited hunk and the surrounding
post-change code, every untrusted byte inside the prompt's per-call boundary
fence and redacted through the same choke point as the diff.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lintro.ai.review.context_windows import fit_content
from lintro.ai.review.prompt_redaction import redact_prompt_text

if TYPE_CHECKING:
    from collections.abc import Sequence

    from lintro.ai.review.models.review_finding import ReviewFinding
    from lintro.ai.review.repo_context import RepoContextSource

__all__ = ["render_verification_findings"]

#: Token allowance for one finding's cited code window.
_CITED_CODE_TOKENS = 1_200


def _cited_code(
    *,
    finding: ReviewFinding,
    source: RepoContextSource | None,
) -> str:
    """Return the post-change code around the finding's cited line.

    Args:
        finding: The finding whose ``file:line`` to show.
        source: Head-side reader, or ``None``.

    Returns:
        A definition-aware window around the line (``fit_content`` over a
        one-line hunk), or an empty string when the file cannot be read.
    """
    if source is None or not finding.file:
        return ""
    content = source.read(finding.file)
    if not content:
        return ""
    line = max(finding.line, 1)
    text, _cut = fit_content(
        path=finding.file,
        content=content,
        hunks=((line, line),),
        allowance=_CITED_CODE_TOKENS,
    )
    return text


def render_verification_findings(
    *,
    findings: Sequence[ReviewFinding],
    indices: Sequence[int],
    source: RepoContextSource | None,
    boundary: str,
    allowed_paths: frozenset[str],
) -> str:
    """Render the selected findings, each fenced, for the user prompt.

    Args:
        findings: The round's findings.
        indices: Which of them to render, in prompt order.
        source: Head-side reader for the cited code.
        boundary: The prompt's per-call boundary marker.
        allowed_paths: The only paths whose head content may be read. A
            finding is model output and may name any file; the synthesis
            pass's findings are not diff-gated before this point, so
            without the check a finding could put an unchanged file's
            content in front of the provider (#2734 review).

    Returns:
        The rendered block; every untrusted byte sits inside the fence.
    """
    blocks: list[str] = []
    for position, index in enumerate(indices, start=1):
        finding = findings[index]
        body = "\n".join(
            [
                f"severity: {finding.severity}",
                f"category: {finding.category}",
                f"confidence: {finding.confidence}",
                f"file: {finding.file}:{finding.line}",
                f"title: {finding.title}",
                f"description: {finding.description}",
                f"cause: {finding.cause}",
                f"failure_scenario: {finding.failure_scenario}",
            ],
        )
        cited = (
            _cited_code(finding=finding, source=source)
            if finding.file in allowed_paths
            else ""
        )
        code = (
            f"\n{finding.file} (post-change, around line {finding.line}):\n{cited}"
            if cited
            else ""
        )
        blocks.append(
            f"Finding {position}:\n<{boundary}>\n"
            f"{redact_prompt_text(text=body, source='finding')}"
            f"{redact_prompt_text(text=code, source='cited code')}\n"
            f"</{boundary}>",
        )
    return "\n\n".join(blocks)

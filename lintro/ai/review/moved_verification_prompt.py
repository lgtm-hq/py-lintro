"""Prompt and answer handling for the moved-finding check (#3466).

The prompt carries, per candidate, what the earlier round recorded (title,
description, rule and fingerprint, the path and line it was reported at and
the code that was there), then the pull request's current diff and the
current content of every file it added, modified or renamed. All of it is
redacted and fenced in the per-call boundary marker.

The answer is one JSON object, ``{"moved_findings": [{"index", "still_present",
"file", "line"}]}``. A malformed entry costs that finding its verdict (it is
held), never the round its check.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from lintro.ai.json_response import strip_json_fences
from lintro.ai.review.context.git_ops import _run_git
from lintro.ai.review.enums.changed_file_status import ChangedFileStatus
from lintro.ai.review.enums.moved_verdict import MovedVerdict
from lintro.ai.review.exceptions import ReviewContextError
from lintro.ai.review.finding_identity import normalize_file_path
from lintro.ai.review.models.delta_plan import LOCATION_HOLDS
from lintro.ai.review.prompt_redaction import redact_prompt_text

if TYPE_CHECKING:
    from collections.abc import Sequence

    from lintro.ai.review.models.finding_record import FindingRecord
    from lintro.ai.review.models.review_context import ReviewContext

__all__ = [
    "parse_moved_answer",
    "render_changed_files",
    "render_candidates",
    "render_diff",
]

#: Lines of recorded code shown on each side of a finding's line.
_SNIPPET_RADIUS = 3

#: Characters per token the budget assumes (:func:`estimate_tokens`'s rate).
_CHARS_PER_TOKEN = 4

#: Spellings of ``still_present`` that mean each verdict.
_ANSWERS = {
    "true": MovedVerdict.PRESENT,
    "false": MovedVerdict.FIXED,
    "unknown": MovedVerdict.UNKNOWN,
}


def _git_show(*, repo_root: str, sha: str, path: str) -> str | None:
    """Return a file's content at a commit, or ``None`` when git cannot.

    Args:
        repo_root: The repository.
        sha: The commit.
        path: The file.

    Returns:
        The content, or ``None``.
    """
    if not repo_root or not sha or sha in LOCATION_HOLDS:
        return None
    try:
        result = _run_git(
            args=["-C", repo_root, "show", f"{sha}:{path}"],
            check=False,
        )
    except ReviewContextError:
        return None
    return result.stdout if result.returncode == 0 else None


def _snippet(*, record: FindingRecord, repo_root: str) -> str:
    """Return the code around a record's line at the head that reported it.

    Args:
        record: The prior record, at its stored location.
        repo_root: The repository.

    Returns:
        Numbered lines around the finding, or ``""`` when git cannot read
        them (an unknown or unreachable head).
    """
    text = _git_show(repo_root=repo_root, sha=record.line_sha, path=record.file)
    if not text or record.line <= 0:
        return ""
    lines = text.splitlines()
    start = max(record.line - _SNIPPET_RADIUS, 1)
    end = min(record.line + _SNIPPET_RADIUS, len(lines))
    return "\n".join(f"{index}: {lines[index - 1]}" for index in range(start, end + 1))


def render_candidates(
    *,
    candidates: Sequence[FindingRecord],
    repo_root: str,
    boundary: str,
) -> str:
    """Render the candidates as numbered, fenced entries.

    Args:
        candidates: The prior records to check.
        repo_root: The repository the recorded code is read from.
        boundary: The per-call fence marker.

    Returns:
        The rendered findings.
    """
    blocks: list[str] = []
    for index, record in enumerate(candidates, start=1):
        body = "\n".join(
            part
            for part in (
                f"title: {record.title}",
                f"rule: {record.category} (fingerprint {record.fingerprint})",
                f"reported at: {record.file}:{record.line}",
                f"description: {record.description or record.title}",
                f"cause: {record.cause}" if record.cause else "",
                f"fix: {record.fix}" if record.fix else "",
                "code there when reported:\n"
                + _snippet(record=record, repo_root=repo_root),
            )
            if part
        )
        text = redact_prompt_text(text=body, source="moved finding")
        blocks.append(f"Finding {index}:\n<{boundary}>\n{text}\n</{boundary}>")
    return "\n\n".join(blocks)


def render_diff(*, context: ReviewContext, budget_tokens: int) -> tuple[str, int]:
    """Return the pull request's diff, cut to the budget.

    Args:
        context: The round's context.
        budget_tokens: Tokens the diff and the files may use together.

    Returns:
        The redacted diff and the tokens left for the files.
    """
    limit = max(budget_tokens, 0) * _CHARS_PER_TOKEN
    diff = context.unified_diff
    if len(diff) > limit:
        diff = diff[:limit] + "\n[diff truncated]"
    text = redact_prompt_text(text=diff, source="PR diff")
    return text, max(budget_tokens - len(text) // _CHARS_PER_TOKEN, 0)


def render_changed_files(
    *,
    context: ReviewContext,
    repo_root: str,
    boundary: str,
    budget_tokens: int,
) -> str:
    """Render the head content of every added, modified or renamed file.

    Args:
        context: The round's context.
        repo_root: The repository holding the head.
        boundary: The per-call fence marker.
        budget_tokens: Tokens the files may use; a file past it is named but
            not shown.

    Returns:
        The fenced files.
    """
    left = max(budget_tokens, 0) * _CHARS_PER_TOKEN
    blocks: list[str] = []
    for item in context.changed_files:
        if item.status is ChangedFileStatus.DELETED:
            continue
        text = _git_show(repo_root=repo_root, sha=context.head_ref, path=item.path)
        if text is None:
            continue
        if len(text) > left:
            blocks.append(f"File: {item.path} (not shown: over the prompt budget)")
            continue
        left -= len(text)
        shown = redact_prompt_text(text=text, source="changed file")
        blocks.append(f"File: {item.path}\n<{boundary}>\n{shown}\n</{boundary}>")
    return "\n\n".join(blocks) or "(no file content available)"


def parse_moved_answer(
    *,
    content: str,
    count: int,
) -> dict[int, tuple[MovedVerdict, str, int]] | None:
    """Parse the answer into ``position -> (verdict, file, line)``.

    Args:
        content: The raw answer text.
        count: How many findings were asked about.

    Returns:
        Verdicts keyed by 1-based position, or ``None`` when the answer is
        not the JSON shape asked for. An entry with an unknown label or an
        out-of-range position is skipped; its finding is held.
    """
    try:
        payload = json.loads(strip_json_fences(content=content))
    except (json.JSONDecodeError, ValueError):
        return None
    items = payload.get("moved_findings") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return None
    verdicts: dict[int, tuple[MovedVerdict, str, int]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        position = item.get("index")
        raw = item.get("still_present")
        label = str(raw).strip().lower() if raw is not None else ""
        verdict = _ANSWERS.get(label)
        if (
            isinstance(position, bool)
            or not isinstance(position, int)
            or not 1 <= position <= count
            or verdict is None
        ):
            continue
        if position in verdicts:
            # Two answers for one finding: unless they agree, neither counts.
            if verdicts[position][0] is not verdict:
                verdicts[position] = (MovedVerdict.UNKNOWN, "", 0)
            continue
        path = item.get("file")
        line = item.get("line")
        verdicts[position] = (
            verdict,
            normalize_file_path(path) if isinstance(path, str) else "",
            line if isinstance(line, int) and not isinstance(line, bool) else 0,
        )
    return verdicts

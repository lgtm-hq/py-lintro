"""Ordering, table-cell and value formatting for the sticky board.

The leaf layer of the sticky renderer: sorting records into the order a
section lists them, and turning one record or value into the text of one
table cell. Nothing here knows which section it is rendering into.
"""

from __future__ import annotations

from lintro.ai.review.enums.finding_match_outcome import FindingMatchOutcome
from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.finding_matcher import review_finding_from_record
from lintro.ai.review.github_constants import _SEVERITY_EMOJI, SHORT_SHA_LENGTH
from lintro.ai.review.github_lifecycle import inline_comment_url
from lintro.ai.review.github_render import sanitize_comment_text
from lintro.ai.review.models.finding_match_result import FindingMatchResult
from lintro.ai.review.models.finding_record import FindingRecord
from lintro.ai.review.models.review_finding import ReviewFinding
from lintro.ai.review.models.run_record import RunRecord
from lintro.ai.review.sticky.constants import (
    _DETAILS_TAG_RE,
    _QUESTION_EMOJI,
    _TITLE_LIMIT,
)


def _record_sort_key(record: FindingRecord) -> tuple[str, str, int]:
    """Return the presentation sort key for a finding record."""
    return (record.severity.value, record.file, record.line)


def _sorted_open_records(
    *,
    records: tuple[FindingRecord, ...],
    limit: int | None,
) -> list[FindingRecord]:
    """Return open records in presentation order, optionally truncated.

    Args:
        records: Every tracked finding record.
        limit: Maximum number to return, or ``None`` for all.

    Returns:
        Open records sorted by severity, then file, then line.
    """
    ordered = sorted(
        (record for record in records if record.status is FindingStatus.OPEN),
        key=_record_sort_key,
    )
    return ordered if limit is None else ordered[:limit]


def _open_prompt_findings(
    *,
    records: tuple[FindingRecord, ...],
    limit: int | None,
) -> tuple[ReviewFinding, ...]:
    """Return every still-open finding the fix-all prompt must cover.

    Built from the same ``_sorted_open_records`` call the Findings table is
    built from, so the prompt and the table cover the identical set in the
    identical order — by construction rather than by two selections agreeing.

    This round's ``result.findings`` is not that set. Under incomplete
    coverage the matcher carries a prior open record on a file this round
    never read (``finding_matcher.match_findings``, the ``unread and not
    left_diff`` branch), so it stays OPEN and lists in the table while being
    absent from ``result.findings``. Reconciling the two by fingerprint would
    also lose an extra ordinal whenever one fingerprint has several open
    records, so the records are simply the source.

    Records carry the merged text of the most recent sighting
    (``finding_matcher`` rebuilds a record from each round's finding), and the
    prompt renders only fields a record persists, so nothing the panel shows
    is lost by reading records rather than findings.

    Args:
        records: Every tracked record, open and resolved.
        limit: Maximum number to return, or ``None`` for all.

    Returns:
        Open findings sorted by severity, then file, then line.
    """
    return tuple(
        review_finding_from_record(record=record)
        for record in _sorted_open_records(records=records, limit=limit)
    )


def _sorted_resolved_records(
    *,
    records: tuple[FindingRecord, ...],
    limit: int | None,
) -> list[FindingRecord]:
    """Return resolved records newest-first, optionally truncated.

    Args:
        records: Every tracked finding record.
        limit: Maximum number to return, or ``None`` for all.

    Returns:
        Resolved records, most recently fixed first so pruning drops the
        oldest history.
    """
    ordered = sorted(
        (record for record in records if record.status is FindingStatus.RESOLVED),
        key=lambda record: (-record.resolved_round, *_record_sort_key(record)),
    )
    return ordered if limit is None else ordered[:limit]


def _delta_cell(*, record: FindingRecord, match: FindingMatchResult) -> str:
    """Render the ``Δ`` cell for one open finding.

    Args:
        record: Open finding record.
        match: Cross-round matching outcome for this round.

    Returns:
        ``**new**``, ``↩ regressed``, or ``—`` for an unchanged finding.
    """
    outcome = match.outcome_for(record=record)
    if outcome is FindingMatchOutcome.NEW:
        return "**new**"
    if outcome is FindingMatchOutcome.REGRESSED:
        return "↩ regressed"
    return "—"


def _finding_cell(
    *,
    record: FindingRecord,
    repo: str,
    pr_number: int | None,
) -> str:
    """Render the title cell, linked to the finding's inline comment.

    Args:
        record: Open finding record.
        repo: ``owner/name`` slug of the repository.
        pr_number: Pull request number.

    Returns:
        The title as a Markdown link to its thread, or plain text when the
        finding has no inline comment (it was never diff-mappable, the posting
        failed, or its id has not been captured yet). Link syntax inside the
        title is neutralized by ``_cell``'s sanitizer, so a model-written
        ``]`` cannot break out of the link label.
    """
    title = _cell(text=record.title, limit=_TITLE_LIMIT)
    url = inline_comment_url(
        repo=repo,
        pr_number=pr_number,
        comment_id=record.inline_comment_id,
    )
    if not url:
        return title
    return f"[{title.replace('[', '(').replace(']', ')')}]({url})"


def _severity_cell(*, record: FindingRecord) -> str:
    """Render the severity cell for a finding record."""
    if record.is_question:
        return f"{_QUESTION_EMOJI} question"
    return f"{_SEVERITY_EMOJI[record.severity]} {record.severity.value}"


def _location(*, record: FindingRecord) -> str:
    """Render a record's ``file:line`` label for a table cell."""
    path = _cell(text=record.file or "(unknown)", limit=120)
    return f"{path}:{record.line}" if record.line > 0 else path


def _cell(*, text: str, limit: int) -> str:
    """Sanitize model text for safe rendering inside a Markdown table cell.

    Args:
        text: Raw model-derived text.
        limit: Maximum length before truncation.

    Returns:
        Text with mentions neutralized, pipes escaped, and newlines collapsed
        so a single cell cannot break the table it sits in.
    """
    safe = sanitize_comment_text(text, limit=limit)
    return safe.replace("|", "\\|").replace("\n", " ").replace("\r", " ").strip()


def _inline_safe(*, text: str, limit: int) -> str:
    """Sanitize model text for embedding *inside* a collapsible.

    On top of the usual mention neutralization, a model-written ``<details>``
    or ``</details>`` is defanged: the folded finding detail lives inside a
    collapsible, so an unescaped closing tag would end it early and let the
    rest of the comment render at the wrong nesting level.

    Args:
        text: Raw model-derived text.
        limit: Maximum length before truncation.

    Returns:
        Text safe to embed within a ``<details>`` block.
    """
    safe = sanitize_comment_text(text, limit=limit)
    return _DETAILS_TAG_RE.sub(r"&lt;\1\2", safe)


def _short_sha(*, sha: str) -> str:
    """Return the display-length prefix of a commit sha, or an empty string."""
    cleaned = sanitize_comment_text(sha, limit=64).strip()[:SHORT_SHA_LENGTH]
    # Escape *after* truncating: escaping first could cut an escape pair in half.
    return cleaned.replace("|", "\\|")


def _transport_label(*, transport: str, auth_mode: str) -> str:
    """Render the transport badge value, never implying a billed amount."""
    parts = [
        sanitize_comment_text(part, limit=40)
        for part in (transport, auth_mode)
        if part.strip()
    ]
    return " · ".join(parts) if parts else "unknown"


def _model_counts(*, runs: list[RunRecord]) -> list[tuple[str, int]]:
    """Count runs per model, sorted by model name.

    Args:
        runs: Run records to count over.

    Returns:
        ``(model, count)`` pairs in stable alphabetical order.
    """
    counts: dict[str, int] = {}
    for run in runs:
        model = run.model or "unknown"
        counts[model] = counts.get(model, 0) + 1
    return sorted(counts.items())


def _fmt_compact(*, value: int) -> str:
    """Format a large count compactly, for example ``24.9k`` or ``1.5M``.

    Args:
        value: Count to format.

    Returns:
        The compact representation. Cumulative token totals across many rounds
        reach seven figures, which must not render as ``1500.0k``.
    """
    if value < 1000:
        return str(value)
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    return f"{value / 1000:.1f}k"


def _plural(*, count: int, noun: str) -> str:
    """Return ``noun`` pluralized for ``count``."""
    return noun if count == 1 else f"{noun}s"

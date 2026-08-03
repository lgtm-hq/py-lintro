"""Parsing of the narrative review outputs (#1907).

The diff review call returns three editorial fields alongside its findings: a
structured ``summary``, ``verdict_reasoning`` and ``file_assessments``. Older
models, prose answers and third-party transports do not produce them, so every
parser here degrades to ``None``/empty rather than raising: a missing narrative
field must never fail a run that produced real findings (the #1853 regression
class).
"""

from __future__ import annotations

from typing import Any

from lintro.ai.review.models.file_assessment import FileAssessment
from lintro.ai.review.models.review_summary import ReviewSummary
from lintro.ai.review.models.summary_bullet import SummaryBullet
from lintro.ai.review.models.verdict_reasoning import VerdictReasoning

__all__ = [
    "MAX_WALKTHROUGH_BULLETS",
    "collapse_to_single_line",
    "parse_file_assessments",
    "parse_narrative",
    "parse_review_summary",
    "parse_summary_text",
    "parse_verdict_reasoning",
]

#: Upper bound on walkthrough bullets kept from a single response. The prompt
#: asks for 3-6; a longer list is truncated rather than rejected.
MAX_WALKTHROUGH_BULLETS: int = 6


def collapse_to_single_line(*, text: str) -> str:
    """Collapse a string to a single whitespace-normalized line.

    Finding titles are rendered inline in tables and headings, so an embedded
    newline breaks the surrounding markup. The constraint is stated in the
    schema and the prompt and enforced here so a non-compliant model cannot
    corrupt a rendered comment.

    Args:
        text: Raw text that should occupy one line.

    Returns:
        The text with all runs of whitespace collapsed to single spaces and
        the ends stripped.
    """
    return " ".join(text.split())


def _as_text(value: object) -> str:
    """Return a stripped string for a raw payload value.

    Args:
        value: Raw value from a parsed model response.

    Returns:
        The stripped string form, or an empty string when the value is absent.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _as_path_tuple(value: object) -> tuple[str, ...]:
    """Return a tuple of non-empty path strings from a raw payload value.

    Args:
        value: Raw value expected to be a list of paths.

    Returns:
        Paths in payload order; empty when the value is not a list.
    """
    if not isinstance(value, list):
        return ()
    paths = (_as_text(item) for item in value)
    return tuple(path for path in paths if path)


def parse_summary_text(*, raw_summary: object) -> str:
    """Extract the plain-text summary from either summary shape.

    The pre-#1907 schema returned ``summary`` as a string. The extended schema
    returns an object; its ``headline`` is the equivalent plain text. Both are
    accepted so the flat ``ReviewResult.summary`` keeps its meaning for every
    existing consumer. The string branch stays reachable through transports
    that do not enforce the strict CLI schema, through the prose recovery
    payload, and through replayed responses recorded before #1907.

    Args:
        raw_summary: Raw ``summary`` value from a parsed model response.

    Returns:
        The summary sentence, or an empty string when unavailable.
    """
    if isinstance(raw_summary, dict):
        return _as_text(raw_summary.get("headline"))
    return _as_text(raw_summary)


def parse_review_summary(*, raw_summary: object) -> ReviewSummary | None:
    """Parse the structured PR summary from a review payload.

    Args:
        raw_summary: Raw ``summary`` value from a parsed model response.

    Returns:
        The parsed summary, or ``None`` when the payload carries only a plain
        string (older models) or nothing usable.
    """
    if not isinstance(raw_summary, dict):
        return None

    headline = _as_text(raw_summary.get("headline"))
    bullets = _parse_walkthrough(raw_walkthrough=raw_summary.get("walkthrough"))
    summary = ReviewSummary(headline=headline, walkthrough=bullets)
    return None if summary.is_empty else summary


def _parse_walkthrough(*, raw_walkthrough: object) -> tuple[SummaryBullet, ...]:
    """Parse walkthrough bullets, accepting plain strings or objects.

    Args:
        raw_walkthrough: Raw ``walkthrough`` value from a summary payload.

    Returns:
        Parsed bullets, capped at :data:`MAX_WALKTHROUGH_BULLETS`.
    """
    if not isinstance(raw_walkthrough, list):
        return ()

    bullets: list[SummaryBullet] = []
    for item in raw_walkthrough:
        if isinstance(item, dict):
            text = _as_text(item.get("text"))
            finding_ref = _as_text(item.get("finding_ref"))
        else:
            text = _as_text(item)
            finding_ref = ""
        if not text:
            continue
        bullets.append(SummaryBullet(text=text, finding_ref=finding_ref))
        if len(bullets) == MAX_WALKTHROUGH_BULLETS:
            break
    return tuple(bullets)


def parse_verdict_reasoning(*, raw_reasoning: object) -> VerdictReasoning | None:
    """Parse the verdict reasoning block from a review payload.

    Args:
        raw_reasoning: Raw ``verdict_reasoning`` value from a parsed response.

    Returns:
        The parsed reasoning, or ``None`` when the field is absent, malformed
        or entirely empty.
    """
    if not isinstance(raw_reasoning, dict):
        return None

    reasoning = VerdictReasoning(
        deciding_factor=_as_text(raw_reasoning.get("deciding_factor")),
        failure_mechanism=_as_text(raw_reasoning.get("failure_mechanism")),
        files_needing_attention=_as_path_tuple(
            raw_reasoning.get("files_needing_attention"),
        ),
    )
    return None if reasoning.is_empty else reasoning


def parse_file_assessments(*, raw_assessments: object) -> tuple[FileAssessment, ...]:
    """Parse per-file assessments from a review payload.

    Args:
        raw_assessments: Raw ``file_assessments`` value from a parsed response.

    Returns:
        Assessments in payload order, one per named file. Entries without a
        file path or a non-empty overview are dropped; the first entry for a
        repeated path wins.
    """
    if not isinstance(raw_assessments, list):
        return ()

    assessments: dict[str, FileAssessment] = {}
    for item in raw_assessments:
        if not isinstance(item, dict):
            continue
        path = _as_text(item.get("file"))
        overview = _as_text(item.get("overview"))
        if not path or not overview or path in assessments:
            continue
        assessments[path] = FileAssessment(file=path, overview=overview)
    return tuple(assessments.values())


def parse_narrative(
    *,
    payload: dict[str, Any],
) -> tuple[ReviewSummary | None, VerdictReasoning | None, tuple[FileAssessment, ...]]:
    """Parse every narrative field from a review payload in one call.

    Args:
        payload: Parsed model response for one review chunk.

    Returns:
        Tuple of structured summary, verdict reasoning and file assessments,
        each degrading to ``None``/empty when the model omitted it.
    """
    return (
        parse_review_summary(raw_summary=payload.get("summary")),
        parse_verdict_reasoning(raw_reasoning=payload.get("verdict_reasoning")),
        parse_file_assessments(raw_assessments=payload.get("file_assessments")),
    )

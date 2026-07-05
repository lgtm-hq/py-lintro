"""Tests for review orchestrator hardening: redaction and severity handling.

Covers issue #1040 (secret redaction reaches the provider payload) and
issue #1041 (severity normalization for the P1 exit gate).
"""

from __future__ import annotations

from assertpy import assert_that
from loguru import logger

from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.pr_metadata import PRMetadata
from lintro.ai.review.models.review_chunk import ReviewChunk
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.models.review_finding import Severity
from lintro.ai.review.models.review_metadata import ReviewMetadata
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.orchestrator import (
    _parse_findings,
    build_review_prompt,
)

_LEAKED_KEY = "sk-abcdefghijklmnopqrstuvwxyz0123456789"


def _placeholder_metadata() -> ReviewMetadata:
    """Return placeholder review metadata for result construction.

    Returns:
        A zeroed ``ReviewMetadata`` suitable for result-only assertions.
    """
    return ReviewMetadata(
        model="",
        provider="",
        context_window=0,
        depth=0,
        chunks_total=0,
        chunks_current=0,
        files_reviewed=0,
        files_total=0,
        checklist_items=0,
    )


def _make_context(*, body: str) -> ReviewContext:
    """Build a minimal review context with a PR body.

    Args:
        body: PR metadata body text.

    Returns:
        A review context wrapping a single changed file.
    """
    return ReviewContext(
        base_ref="main",
        head_ref="feature",
        changed_files=[
            ChangedFile(
                path="src/main.py",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
        unified_diff="diff",
        pr_metadata=PRMetadata(
            title="Rotate keys",
            body=body,
            number=7,
            repo="owner/repo",
        ),
    )


def _make_chunk(*, diff: str) -> ReviewChunk:
    """Build a single-file review chunk with the given diff.

    Args:
        diff: Diff content for the chunk.

    Returns:
        A review chunk covering ``src/main.py``.
    """
    return ReviewChunk(
        id=1,
        files=["src/main.py"],
        diff=diff,
        relationship="single-file",
    )


def test_build_review_prompt_redacts_secrets_in_diff() -> None:
    """An API key embedded in the diff is redacted in the rendered prompt."""
    chunk = _make_chunk(diff=f"+api_key = '{_LEAKED_KEY}'\n")
    context = _make_context(body="Routine change.")

    _system, user_prompt = build_review_prompt(
        chunk=chunk,
        context=context,
        checklist_text="1. [logic-bug] Example?",
        checklist_count=1,
        interaction_paths="(none)",
    )

    assert_that(user_prompt).does_not_contain(_LEAKED_KEY)
    assert_that(user_prompt).contains("[REDACTED]")


def test_build_review_prompt_redacts_secrets_in_pr_body() -> None:
    """A secret in PR metadata body is redacted before reaching the prompt."""
    chunk = _make_chunk(diff="+harmless change\n")
    context = _make_context(body=f"Deploy token: {_LEAKED_KEY}")

    _system, user_prompt = build_review_prompt(
        chunk=chunk,
        context=context,
        checklist_text="1. [logic-bug] Example?",
        checklist_count=1,
        interaction_paths="(none)",
    )

    assert_that(user_prompt).does_not_contain(_LEAKED_KEY)
    assert_that(user_prompt).contains("[REDACTED]")


def test_build_review_prompt_logs_secret_warning() -> None:
    """Detected secrets emit a warning before the prompt is dispatched."""
    chunk = _make_chunk(diff=f"+token = {_LEAKED_KEY}\n")
    context = _make_context(body="Routine change.")

    messages: list[str] = []
    handler_id = logger.add(
        lambda message: messages.append(str(message)),
        level="WARNING",
    )
    try:
        build_review_prompt(
            chunk=chunk,
            context=context,
            checklist_text="1. [logic-bug] Example?",
            checklist_count=1,
            interaction_paths="(none)",
        )
    finally:
        logger.remove(handler_id)

    joined = "".join(messages)
    assert_that(joined).contains("Redacted")
    assert_that(joined).contains("secret")


def test_severity_normalization_lowercase() -> None:
    """A lowercase 'p1' severity normalizes to Severity.P1."""
    findings = _parse_findings(
        raw_findings=[{"severity": "p1", "title": "Bug"}],
    )

    assert_that(findings).is_length(1)
    assert_that(findings[0].severity).is_equal_to(Severity.P1)


def test_severity_normalization_whitespace() -> None:
    """A trailing-space 'P1 ' severity normalizes to Severity.P1."""
    findings = _parse_findings(
        raw_findings=[{"severity": "P1 ", "title": "Bug"}],
    )

    assert_that(findings).is_length(1)
    assert_that(findings[0].severity).is_equal_to(Severity.P1)


def test_severity_unknown_maps_to_p3() -> None:
    """An unknown severity like 'critical' maps to Severity.P3."""
    findings = _parse_findings(
        raw_findings=[{"severity": "critical", "title": "Bug"}],
    )

    assert_that(findings).is_length(1)
    assert_that(findings[0].severity).is_equal_to(Severity.P3)


def test_has_p1_findings_after_lowercase_normalization() -> None:
    """The exit gate fires when a lowercase 'p1' finding is normalized."""
    findings = _parse_findings(
        raw_findings=[{"severity": "p1", "title": "Bug", "file": "a.py", "line": 1}],
    )
    result = ReviewResult(
        metadata=_placeholder_metadata(),
        summary="s",
        checklist=(),
        findings=findings,
    )

    assert_that(result.has_p1_findings).is_true()


def test_has_p1_findings_false_for_unknown_severity() -> None:
    """An unknown severity does not trip the P1 exit gate (maps to P3)."""
    findings = _parse_findings(
        raw_findings=[{"severity": "blocker", "title": "Bug"}],
    )
    result = ReviewResult(
        metadata=_placeholder_metadata(),
        summary="s",
        checklist=(),
        findings=findings,
    )

    assert_that(result.has_p1_findings).is_false()

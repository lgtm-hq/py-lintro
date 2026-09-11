"""The CLI applies the posting policy before any surface reads the result.

Companion to the GitHub-surface test in
``tests/unit/ai/review/test_github.py``: that one covers the single place a
thread is opened, this one covers the single place ``lintro review`` marks
``posted_inline`` (#2572). The gate is worthless if the terminal output, the
JSON payload, or ``--post`` ever sees findings the policy has not touched, so
the apply point is asserted to run ahead of ``_emit_output``.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest
from assertpy import assert_that

from lintro.ai.config import AIConfig
from lintro.ai.review.enums.review_verdict import ReviewVerdict
from lintro.ai.review.finding_matcher import (
    count_blocking_findings,
    derive_verdict,
)
from lintro.ai.review.models.finding_record import FindingRecord
from lintro.ai.review.models.review_finding import ReviewFinding, Severity
from lintro.ai.review.models.review_metadata import ReviewMetadata
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.sticky_request import StickyRequest
from lintro.ai.review.sticky.assembly import advance_review_state
from lintro.cli_utils.commands import review as review_command
from lintro.enums.confidence_level import ConfidenceLevel


def _finding(**overrides: Any) -> ReviewFinding:
    """Build a review finding for the CLI apply-point test.

    Args:
        **overrides: Fields to override on the base finding.

    Returns:
        The constructed finding.
    """
    fields: dict[str, Any] = {
        "severity": Severity.P2,
        "category": "logic-bug",
        "file": "src/app.py",
        "line": 12,
        "title": "Posted as a thread",
        "description": "The branch is never taken.",
        "cause": "Off by one.",
        "fix": "Compare with >=.",
        "confidence": "high",
    }
    fields.update(overrides)
    return ReviewFinding(**fields)


@pytest.fixture
def emitted(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Stub every step around the apply point and capture what it emits.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        A dict the stubbed ``_emit_output`` fills in.
    """
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        review_command,
        "validate_result_suggested_patches",
        lambda *, result, context: result,
    )
    monkeypatch.setattr(
        review_command,
        "build_prompt_question_map",
        lambda *, items: {},
    )
    monkeypatch.setattr(
        review_command,
        "enrich_review_result",
        lambda *, result, question_map: result,
    )
    monkeypatch.setattr(
        review_command,
        "_execute_advisory",
        lambda **kwargs: [],
    )
    monkeypatch.setattr(
        review_command,
        "_emit_output",
        lambda **kwargs: captured.update(result=kwargs["result"]),
    )
    return captured


def _result(*findings: ReviewFinding) -> ReviewResult:
    """Wrap findings in a minimal completed review result.

    Args:
        *findings: Findings the round reported.

    Returns:
        The constructed result.
    """
    return ReviewResult(
        metadata=ReviewMetadata(
            model="m",
            provider="p",
            context_window=1000,
            depth=1,
            chunks_total=1,
            chunks_current=1,
            files_reviewed=1,
            files_total=1,
            checklist_items=0,
        ),
        summary="s",
        findings=findings,
    )


def _run(*, result: ReviewResult) -> None:
    """Drive ``_render_post_and_exit`` over a stubbed run.

    Args:
        result: The review result the run completed with.
    """
    options = SimpleNamespace(
        show_checklist=None,
        advisory_tools=(),
        tool_options=None,
        post=False,
        fail_on_findings=False,
    )
    lintro_config = SimpleNamespace(review=SimpleNamespace(checklist_display=None))
    prepared = SimpleNamespace(
        ai_config=AIConfig(review_inline_min_confidence=ConfidenceLevel.HIGH),
        context=SimpleNamespace(changed_files=()),
        checklist_items=(),
        workspace_root=".",
    )
    # The stubs above mean only the fields named here are ever read, so the
    # namespaces stand in for the real option, config and target dataclasses.
    with pytest.raises(SystemExit):
        review_command._render_post_and_exit(
            options=cast(Any, options),
            lintro_config=cast(Any, lintro_config),
            prepared=cast(Any, prepared),
            result=result,
            prior_state=ReviewState(),
            targets=cast(Any, SimpleNamespace()),
            resolved_profile=cast(Any, SimpleNamespace()),
        )


def test_the_cli_marks_findings_before_any_surface_reads_them(
    emitted: dict[str, Any],
) -> None:
    """A finding below the configured floor reaches output already gated."""
    result = _result(
        _finding(),
        _finding(title="Kept as a note", confidence="medium"),
    )

    _run(result=result)

    flags = {
        finding.title: finding.posted_inline for finding in emitted["result"].findings
    }
    assert_that(flags).is_equal_to(
        {"Posted as a thread": True, "Kept as a note": False},
    )


# --- state persistence (review thread on #2583, round 2) ----------------------


@pytest.fixture
def persisted(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Stub the provider and the orchestrator, capture what the round persists.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        A dict the stubbed ``persist_review_state`` fills in.
    """
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        review_command,
        "get_provider",
        lambda *args, **kwargs: SimpleNamespace(name="stub"),
    )
    monkeypatch.setattr(
        review_command,
        "_stamp_metadata",
        lambda *, result, stamp: result,
    )
    monkeypatch.setattr(
        review_command,
        "persist_review_state",
        lambda **kwargs: captured.update(kwargs),
    )
    return captured


def _round(
    *,
    monkeypatch: pytest.MonkeyPatch,
    result: ReviewResult,
    post: bool,
) -> ReviewResult:
    """Drive ``_run_round`` over a stubbed provider and orchestrator.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        result: The result the orchestrator returns for this round.
        post: Whether the run was invoked with ``--post``.

    Returns:
        The result the round hands back to the render/post tail.
    """
    monkeypatch.setattr(
        review_command,
        "execute_review",
        lambda prepared, *, provider, policy: result,
    )
    options = SimpleNamespace(post=post, force_full=False, output_format="terminal")
    prepared = SimpleNamespace(
        ai_config=AIConfig(),
        context=SimpleNamespace(changed_files=(), head_ref="deadbeef", base_ref="main"),
        workspace_root=".",
    )
    policy = SimpleNamespace(prior_state=None)
    return review_command._run_round(
        options=cast(Any, options),
        prepared=cast(Any, prepared),
        policy=cast(Any, policy),
        stamp=cast(Any, SimpleNamespace()),
        targets=cast(Any, SimpleNamespace(state_pr=7, effective_repo="o/r")),
        console=cast(Any, SimpleNamespace()),
    )


def _records(*, result: ReviewResult) -> tuple[FindingRecord, ...]:
    """Track a persisted result the way ``persist_review_state`` would.

    Args:
        result: The result handed to the persist call.

    Returns:
        The finding records the store would carry into the next round.
    """
    return advance_review_state(
        request=StickyRequest(result=result, prior_state=None, head_sha="deadbeef"),
    ).findings


@pytest.mark.parametrize("post", [True, False], ids=["posted", "not-posted"])
def test_a_gated_p1_never_reaches_the_state_store_as_an_open_record(
    monkeypatch: pytest.MonkeyPatch,
    persisted: dict[str, Any],
    post: bool,
) -> None:
    """The store is marked before it is written, with or without ``--post``.

    The store is what the next round matches against, so an unmarked write
    would record a note as an open inline finding: the next board would read
    BLOCKED and a converged skip would exit 1 on a finding the verdict of the
    round that found it deliberately ignored.
    """
    gated = _finding(severity=Severity.P1, confidence="low", title="Only a note")

    _round(monkeypatch=monkeypatch, result=_result(gated), post=post)

    written = persisted["result"]
    assert_that([f.posted_inline for f in written.findings]).is_equal_to([False])
    records = _records(result=written)
    assert_that(records).is_empty()
    assert_that(derive_verdict(findings=records)).is_not_equal_to(
        ReviewVerdict.BLOCKED,
    )
    assert_that(count_blocking_findings(findings=records)).is_equal_to(0)

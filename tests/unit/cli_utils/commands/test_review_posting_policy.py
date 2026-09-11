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
from lintro.ai.review.models.review_finding import ReviewFinding, Severity
from lintro.ai.review.models.review_metadata import ReviewMetadata
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.review_state import ReviewState
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

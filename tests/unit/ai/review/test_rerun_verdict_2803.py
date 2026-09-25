"""A rerun at the same head reaches the attempt's verdict unless it redid it.

Each test resumes a hand-built state: every file covered at the head, and the
latest run at that head carrying one recorded degradation, which is the state
run 36008083307 attempt 1 left behind (#2803). The review runs end to end on a
recording fake transport, and its JSON envelope goes through the CI outcome
classifier, so each test asserts the provider calls and the exit code the job
would end with.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport
from lintro.ai.exceptions import AIProviderError
from lintro.ai.providers.capabilities import ProviderCapabilities
from lintro.ai.providers.response import AIResponse
from lintro.ai.review.coverage_degradation import (
    GENERATED_QUESTIONS_FAILED_NOTE,
    format_question_pass_note,
)
from lintro.ai.review.enums.coverage_degradation_reason import (
    CoverageDegradationReason,
)
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.coverage_degradation import (
    CARRIED_CHUNK_INDEX,
    SYNTHESIS_CHUNK_INDEX,
    CoverageDegradation,
)
from lintro.ai.review.models.coverage_record import CoverageRecord
from lintro.ai.review.models.degradation_record import DegradationRecord
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.models.review_metadata import ReviewMetadata
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.run_coverage import RunCoverage
from lintro.ai.review.models.run_identity import RunIdentity
from lintro.ai.review.models.run_record import RunRecord
from lintro.ai.review.orchestrator import run_review
from lintro.ai.review.output import review_result_to_json
from lintro.ai.review.patch_hash import normalized_patch_hash
from lintro.ai.review.pr_budget import PR_BUDGET_REASON_PREFIX, PrBudget
from lintro.ai.review.session import ReviewSessionOptions

_HEAD = "feature"
_PATHS = ("a.py", "b.py")
_Reason = CoverageDegradationReason
_SCRIPT = (
    Path(__file__).resolve().parents[4]
    / "scripts"
    / "ci"
    / "classify_review_outcome.py"
)


@pytest.fixture
def classifier() -> ModuleType:
    """Load the CI outcome classifier script as a module.

    Returns:
        The classifier module.

    Raises:
        RuntimeError: When the module spec cannot be created.
    """
    spec = importlib.util.spec_from_file_location("classify_review_outcome", _SCRIPT)
    if spec is None or spec.loader is None:
        msg = f"Unable to load module from {_SCRIPT}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    sys.modules["classify_review_outcome"] = module
    spec.loader.exec_module(module)
    return module


def _diff(path: str) -> str:
    """Return a one-line unified diff for ``path``.

    Args:
        path: Repository-relative file path.

    Returns:
        The diff.
    """
    return (
        f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
        f"@@ -1,1 +1,2 @@\n context\n+change-{path}\n"
    )


def _context() -> ReviewContext:
    """Return the two-file review context at ``_HEAD``.

    Returns:
        The context.
    """
    return ReviewContext(
        base_ref="main",
        head_ref=_HEAD,
        changed_files=[
            ChangedFile(path=path, status="modified", additions=1, deletions=0)
            for path in _PATHS
        ],
        unified_diff="\n".join(_diff(path) for path in _PATHS),
        pr_metadata=None,
    )


def _degraded_state(degradation: CoverageDegradation) -> ReviewState:
    """Return a state with every file covered and one degraded run at head.

    Args:
        degradation: What the earlier attempt recorded.

    Returns:
        The state a rerun resumes.
    """
    return ReviewState(
        runs=(
            RunRecord(
                identity=RunIdentity(round=1, sha=_HEAD),
                coverage=RunCoverage(
                    files_reviewed=len(_PATHS),
                    degradations=(
                        DegradationRecord.from_degradation(
                            degradation=degradation,
                            head_sha=_HEAD,
                        ),
                    ),
                ),
            ),
        ),
        coverage=tuple(
            CoverageRecord(
                path=path,
                patch_hash=normalized_patch_hash(_diff(path)),
                reviewed_sha=_HEAD,
            )
            for path in _PATHS
        ),
    )


def _response(content: str) -> AIResponse:
    """Wrap model text in a provider response costing $0.01.

    Args:
        content: The answer text.

    Returns:
        The response.
    """
    return AIResponse(
        content=content,
        model="fake-model",
        input_tokens=10,
        output_tokens=5,
        cost_estimate=0.01,
        provider="fake",
    )


_MAIN_ANSWER = (
    '{"summary": "ok", "checklist": [], "findings": [{"severity": "P3",'
    ' "category": "logic-bug", "file": "a.py", "line": 1, "title": "Nit",'
    ' "description": "d", "cause": "c", "fix": "f", "confidence": "high"}]}'
)
_QUESTIONS = '{"generated_questions": [{"question": "Is it used?"}]}'


def _run(
    *,
    prior: ReviewState,
    calls: list[str],
    depth: int = 1,
    fail_calls: frozenset[int] = frozenset(),
    pr_budget: PrBudget | None = None,
) -> ReviewResult:
    """Rerun the review over ``prior`` with a recording fake transport.

    Args:
        prior: The state the rerun resumes.
        calls: Receives one entry per provider call (the prompt's start).
        depth: Review depth.
        fail_calls: 1-based call numbers that time out.
        pr_budget: The PR budget for the round, if any.

    Returns:
        The review result.
    """
    provider = MagicMock()
    provider.aclose = AsyncMock()
    provider.model_name = "fake-model"
    provider.name = "fake"
    provider.capabilities = ProviderCapabilities(supports_sessions=False)

    def _call_ai(*, budget: Any = None, **kwargs: Any) -> AIResponse:
        calls.append(str(kwargs.get("user_prompt", ""))[:40])
        if len(calls) in fail_calls:
            raise AIProviderError("Claude CLI timed out after 600s")
        answer = _QUESTIONS if depth >= 2 and len(calls) == 1 else _MAIN_ANSWER
        response = _response(answer)
        if budget is not None:
            budget.record(response.cost_estimate)
        return response

    with patch("lintro.ai.review.provider_call.call_ai", side_effect=_call_ai):
        return run_review(
            _context(),
            options=ReviewSessionOptions(
                provider=provider,
                ai_config=AIConfig(
                    enabled=True,
                    review=True,
                    transport=AITransport.API,
                    max_parallel_calls=1,
                ),
                depth=depth,
                checklist_items=[],
                checklist_text="1. [logic-bug] Example?",
                classifications=[],
                prior_state=prior,
                pr_budget=pr_budget,
            ),
        )


def _classify(classifier: ModuleType, result: ReviewResult) -> Any:
    """Classify the result's JSON envelope as the CI job would.

    Args:
        classifier: The loaded classifier module.
        result: The review result.

    Returns:
        The classifier's outcome report.
    """
    return classifier.classify(status=0, output=review_result_to_json(result=result))


def _reasons(result: ReviewResult) -> list[CoverageDegradationReason]:
    """Return the reasons the result recorded.

    Args:
        result: The review result.

    Returns:
        The reasons, in order.
    """
    return [item.reason for item in result.metadata.coverage_degradations]


def test_a_failed_question_pass_is_rewarned_without_a_call(
    classifier: ModuleType,
) -> None:
    """Nothing to redo: no call, exit 0, and attempt 1's warning, verbatim.

    Args:
        classifier: The loaded classifier module.
    """
    failed = CoverageDegradation(
        reason=_Reason.GENERATED_QUESTIONS_FAILED,
        chunk_index=SYNTHESIS_CHUNK_INDEX,
        split=False,
        detail="not_json; retried once",
    )
    calls: list[str] = []

    result = _run(prior=_degraded_state(failed), calls=calls)
    report = _classify(classifier, result)

    attempt_1 = classifier.classify(
        status=0,
        output=review_result_to_json(
            result=ReviewResult(
                metadata=_attempt_1_metadata(failed),
                summary="",
                findings=(),
            ),
        ),
    )
    assert_that(calls).is_empty()
    assert_that(result.metadata.coverage_degradations).is_equal_to((failed,))
    assert_that(result.metadata.findings_coverage_complete).is_true()
    assert_that(report.exit_code).is_equal_to(0)
    assert_that(report.notes).is_equal_to((GENERATED_QUESTIONS_FAILED_NOTE,))
    assert_that(report.notes).is_equal_to(attempt_1.notes)
    assert_that(attempt_1.exit_code).is_equal_to(0)
    assert_that(format_question_pass_note(metadata=result.metadata)).is_equal_to(
        GENERATED_QUESTIONS_FAILED_NOTE,
    )


def _attempt_1_metadata(degradation: CoverageDegradation) -> ReviewMetadata:
    """Return the metadata of the attempt that first recorded ``degradation``.

    Args:
        degradation: What the attempt recorded.

    Returns:
        A complete two-file round's metadata.
    """
    return ReviewMetadata(
        model="fake-model",
        provider="fake",
        context_window=1,
        depth=2,
        chunks_total=1,
        chunks_current=1,
        files_reviewed=len(_PATHS),
        files_total=len(_PATHS),
        checklist_items=0,
        coverage_degradations=(degradation,),
    )


def test_a_failed_sweep_is_redone_for_its_files_and_clears(
    classifier: ModuleType,
) -> None:
    """One call, for the degraded chunk's file only; the redo passes the check.

    Args:
        classifier: The loaded classifier module.
    """
    calls: list[str] = []

    result = _run(
        prior=_degraded_state(
            CoverageDegradation(
                reason=_Reason.ADVERSARIAL_SWEEP_FAILED,
                chunk_index=0,
                paths=("a.py",),
            ),
        ),
        calls=calls,
    )
    report = _classify(classifier, result)

    assert_that(calls).is_length(1)
    assert_that(result.metadata.reviewed_paths).is_equal_to(("a.py",))
    assert_that(result.metadata.coverage_degradations).is_empty()
    assert_that(report.exit_code).is_equal_to(0)


def test_a_sweep_that_fails_again_fails_the_rerun(classifier: ModuleType) -> None:
    """The redo's own sweep times out: the reason is recorded again, exit 1.

    Args:
        classifier: The loaded classifier module.
    """
    calls: list[str] = []

    # Depth 3: call 1 is the question pass, 2 the main pass, 3 the sweep.
    result = _run(
        prior=_degraded_state(
            CoverageDegradation(
                reason=_Reason.ADVERSARIAL_SWEEP_FAILED,
                chunk_index=0,
                paths=("a.py",),
            ),
        ),
        calls=calls,
        depth=3,
        fail_calls=frozenset({3}),
    )
    report = _classify(classifier, result)

    assert_that(calls).is_length(3)
    assert_that(_reasons(result)).is_equal_to([_Reason.ADVERSARIAL_SWEEP_FAILED])
    # The new row names its chunk's files, so the next rerun can redo them.
    (row,) = result.metadata.coverage_degradations
    assert_that(row.paths).contains("a.py")
    assert_that(row.chunk_index).is_equal_to(0)
    assert_that(report.outcome).is_equal_to(classifier.ReviewOutcome.DEGRADED)
    assert_that(report.exit_code).is_equal_to(1)


def test_a_turn_limited_chunk_is_reviewed_again(classifier: ModuleType) -> None:
    """Never ``partial: false`` with nothing reviewed and nothing recorded.

    Args:
        classifier: The loaded classifier module.
    """
    calls: list[str] = []

    result = _run(
        prior=_degraded_state(
            CoverageDegradation(
                reason=_Reason.TURN_LIMIT_REACHED,
                chunk_index=0,
                split=False,
                limit=12,
                paths=("a.py",),
            ),
        ),
        calls=calls,
    )
    coverage = result.coverage
    assert coverage is not None

    assert_that(calls).is_length(1)
    assert_that(coverage.reviewed).is_equal_to(1)
    laundered = (
        not result.metadata.partial
        and coverage.reviewed == 0
        and not result.metadata.coverage_degradations
    )
    assert_that(laundered).is_false()
    assert_that(_classify(classifier, result).exit_code).is_equal_to(0)


def test_a_redo_the_pr_budget_blocks_keeps_the_reason_and_fails(
    classifier: ModuleType,
) -> None:
    """A spent PR budget stops the redo before any call; the reason stays.

    Args:
        classifier: The loaded classifier module.
    """
    calls: list[str] = []

    result = _run(
        prior=_degraded_state(
            CoverageDegradation(
                reason=_Reason.ADVERSARIAL_SWEEP_FAILED,
                chunk_index=0,
                paths=("a.py",),
            ),
        ),
        calls=calls,
        pr_budget=PrBudget(budget_usd=40.0, prior_spend_usd=40.0, enforced=True),
    )
    report = _classify(classifier, result)

    assert_that(calls).is_empty()
    assert_that(result.metadata.stopped_reason).starts_with(PR_BUDGET_REASON_PREFIX)
    (carried,) = result.metadata.coverage_degradations
    assert_that(carried.reason).is_equal_to(_Reason.ADVERSARIAL_SWEEP_FAILED)
    assert_that(carried.chunk_index).is_equal_to(CARRIED_CHUNK_INDEX)
    assert_that(report.outcome).is_equal_to(classifier.ReviewOutcome.PR_BUDGET)
    assert_that(report.exit_code).is_equal_to(1)


def test_a_redo_is_charged_to_the_pr_budget(classifier: ModuleType) -> None:
    """The redo spends through the round's budget like any first-round call.

    Args:
        classifier: The loaded classifier module.
    """
    calls: list[str] = []

    result = _run(
        prior=_degraded_state(
            CoverageDegradation(
                reason=_Reason.OUTPUT_EXHAUSTION_RETRIED,
                chunk_index=0,
                paths=("a.py",),
            ),
        ),
        calls=calls,
        pr_budget=PrBudget(budget_usd=40.0, prior_spend_usd=30.0, enforced=True),
    )

    assert_that(calls).is_length(1)
    assert_that(result.metadata.cost_estimate_usd).is_close_to(0.01, 1e-9)
    assert_that(_classify(classifier, result).exit_code).is_equal_to(0)


def test_a_new_head_carries_nothing(classifier: ModuleType) -> None:
    """The same degraded state one push later resumes as before #2803.

    Args:
        classifier: The loaded classifier module.
    """
    state = _degraded_state(
        CoverageDegradation(
            reason=_Reason.ADVERSARIAL_SWEEP_FAILED,
            chunk_index=0,
            paths=("a.py",),
        ),
    )
    moved = ReviewState(
        runs=(
            RunRecord(
                identity=RunIdentity(round=1, sha="older-head"),
                coverage=state.runs[0].coverage,
            ),
        ),
        coverage=state.coverage,
    )
    calls: list[str] = []

    result = _run(prior=moved, calls=calls)

    assert_that(calls).is_empty()
    assert_that(result.metadata.coverage_degradations).is_empty()
    assert_that(_classify(classifier, result).exit_code).is_equal_to(0)

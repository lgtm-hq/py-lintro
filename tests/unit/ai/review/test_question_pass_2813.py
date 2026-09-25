"""The per-PR question pass on Opus 5.5 (#2813, rulings 21-24).

The pass accepted only ``{"generated_questions": [...]}``, so a model that
answered with a bare list, or the list under another key, failed the pass
("payload had no list"). These tests pin:

* the tolerant grammar, and what stays rejected, from answer-shape fixtures;
* the failure kinds, recorded on the degradation's ``detail``;
* the one retry (``turn_limit`` and ``not_json`` only) and how it is recorded;
* the redacted, single-line capture of a failed answer;
* that ``detail`` is additive: the CI classifier's outcome is unchanged.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest
from assertpy import assert_that
from loguru import logger

from lintro.ai.cli_bounds import CallShape
from lintro.ai.exceptions import (
    AICostBudgetExceededError,
    AIProviderError,
    AITurnLimitError,
)
from lintro.ai.review.enums.coverage_degradation_reason import (
    CoverageDegradationReason,
)
from lintro.ai.review.enums.question_failure_kind import QuestionFailureKind
from lintro.ai.review.interrupt import SIGTERM_TIMEOUT_MESSAGE
from lintro.ai.review.merge import ChunkReviewPartial
from lintro.ai.review.models.coverage_degradation import CoverageDegradation
from lintro.ai.review.models.run_questions import RunQuestions
from lintro.ai.review.question_attempts import run_with_one_retry
from lintro.ai.review.question_parse import (
    CAPTURE_CHARS,
    capture_for_log,
    parse_questions,
)
from lintro.ai.review.question_pass import question_pass_degradations

REPO_ROOT = Path(__file__).resolve().parents[4]
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "ai" / "question_pass"
QUESTION = "Does the caller grant the scope?"


def _fixture(name: str) -> str:
    """Return one answer-shape fixture.

    Args:
        name: The fixture's file name.

    Returns:
        Its text.
    """
    return (FIXTURES / name).read_text(encoding="utf-8")


# --- the grammar ---------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("canonical.txt", id="canonical"),
        pytest.param("bare_list.txt", id="bare-list"),
        pytest.param("fenced_bare_list.txt", id="fenced-bare-list"),
        pytest.param("single_key_object.txt", id="single-key-object"),
        pytest.param("prose_wrapped_object.txt", id="prose-wrapped"),
    ],
)
def test_the_accepted_shapes_yield_the_question(name: str) -> None:
    """Each accepted list source yields the same question.

    Args:
        name: The answer-shape fixture.
    """
    parsed = parse_questions(_fixture(name))

    assert_that(parsed.failure).is_none()
    assert_that(parsed.questions).is_equal_to((QUESTION,))


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        pytest.param("strings_list.txt", QuestionFailureKind.NO_QUESTION, id="strings"),
        pytest.param("two_key_object.txt", QuestionFailureKind.NOT_LIST, id="two-keys"),
        pytest.param("nested_deeper.txt", QuestionFailureKind.NOT_LIST, id="nested"),
        pytest.param(
            "not_a_list_value.txt",
            QuestionFailureKind.NOT_LIST,
            id="value-not-list",
        ),
        pytest.param("empty.txt", QuestionFailureKind.EMPTY, id="empty"),
        pytest.param("not_json.txt", QuestionFailureKind.NOT_JSON, id="not-json"),
    ],
)
def test_the_rejected_shapes_name_their_kind(
    name: str,
    kind: QuestionFailureKind,
) -> None:
    """Everything outside the grammar is rejected, with its kind.

    Args:
        name: The answer-shape fixture.
        kind: The expected failure kind.
    """
    parsed = parse_questions(_fixture(name))

    assert_that(parsed.questions).is_empty()
    assert_that(parsed.failure).is_equal_to(kind)


def test_a_list_of_blank_questions_is_no_question() -> None:
    """A list whose objects carry only blank questions is ``no_question``."""
    parsed = parse_questions(json.dumps([{"question": "  "}, {"id": "G2"}]))

    assert_that(parsed.failure).is_equal_to(QuestionFailureKind.NO_QUESTION)


# --- the capture --------------------------------------------------------------


def test_the_capture_is_redacted_bounded_and_one_line() -> None:
    """Secrets are redacted and a ``::`` line cannot become a workflow command."""
    secret = "ghp_" + "a" * 36
    answer = f"line one {secret}\n::error::injected\n" + "x" * 2000

    capture = capture_for_log(answer)

    assert_that(capture).does_not_contain(secret)
    assert_that(capture).contains("[REDACTED]")
    assert_that(capture).does_not_contain("\n")
    assert_that(capture.startswith('"')).is_true()
    assert_that(len(json.loads(capture))).is_less_than_or_equal_to(CAPTURE_CHARS)


# --- the one retry ------------------------------------------------------------


def _usage(tokens: int, cost: float) -> ChunkReviewPartial:
    return ChunkReviewPartial(
        findings=(),
        input_tokens=tokens,
        output_tokens=1,
        cost_estimate=cost,
    )


def _ok() -> RunQuestions:
    return RunQuestions(text=f"G1. {QUESTION}", count=1, usage=_usage(10, 0.01))


def _failed(kind: QuestionFailureKind) -> RunQuestions:
    return RunQuestions(failed=True, failure_kind=kind, usage=_usage(10, 0.01))


class _Script:
    """A scripted ``generate``: returns or raises its outcomes in order."""

    def __init__(self, *outcomes: RunQuestions | Exception) -> None:
        self.outcomes = list(outcomes)
        self.shapes: list[CallShape] = []

    async def __call__(self, shape: CallShape) -> RunQuestions:
        self.shapes.append(shape)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


_FIRST_SHAPE = CallShape(use_one_shot=True, no_tools=False)


async def test_a_turn_limit_is_retried_single_shot_and_can_succeed() -> None:
    """After a turn limit the retry runs with no tools; usage sums both."""
    script = _Script(
        AITurnLimitError(
            "turn limit",
            input_tokens=700,
            output_tokens=30,
            cost_estimate=0.07,
        ),
        _ok(),
    )

    result = await run_with_one_retry(generate=script, shape=_FIRST_SHAPE)

    assert_that(result.failed).is_false()
    assert_that(result.retried).is_true()
    assert_that(script.shapes).is_equal_to(
        [_FIRST_SHAPE, CallShape(use_one_shot=True, no_tools=True)],
    )
    assert_that(result.usage.input_tokens).is_equal_to(710)
    assert_that(result.usage.cost_estimate).is_close_to(0.08, 1e-9)
    assert_that(question_pass_degradations(questions=result)).is_empty()


async def test_not_json_is_retried_with_the_same_shape() -> None:
    """A non-JSON answer gets one more attempt, unchanged."""
    script = _Script(_failed(QuestionFailureKind.NOT_JSON), _ok())

    result = await run_with_one_retry(generate=script, shape=_FIRST_SHAPE)

    assert_that(result.failed).is_false()
    assert_that(script.shapes).is_equal_to([_FIRST_SHAPE, _FIRST_SHAPE])


async def test_a_failed_retry_records_the_first_kind_and_the_retry() -> None:
    """Both attempts fail: the degradation says which kind, and that it retried."""
    script = _Script(
        _failed(QuestionFailureKind.NOT_JSON),
        _failed(QuestionFailureKind.NOT_JSON),
    )

    result = await run_with_one_retry(generate=script, shape=_FIRST_SHAPE)

    assert_that(result.failed).is_true()
    assert_that(result.failure_kind).is_equal_to(QuestionFailureKind.NOT_JSON)
    assert_that(result.usage.input_tokens).is_equal_to(20)
    (degradation,) = question_pass_degradations(questions=result)
    assert_that(degradation.reason).is_equal_to(
        CoverageDegradationReason.GENERATED_QUESTIONS_FAILED,
    )
    assert_that(degradation.detail).is_equal_to("not_json; retried once")


@pytest.mark.parametrize(
    "first",
    [
        pytest.param(_failed(QuestionFailureKind.EMPTY), id="empty"),
        pytest.param(_failed(QuestionFailureKind.NOT_LIST), id="not-list"),
        pytest.param(_failed(QuestionFailureKind.NO_QUESTION), id="no-question"),
        pytest.param(AIProviderError("server error"), id="call-failed"),
    ],
)
async def test_other_kinds_are_never_retried(first: RunQuestions | Exception) -> None:
    """``empty``, ``not_list``, ``no_question`` and ``call_failed``: one call.

    Args:
        first: The only attempt's outcome.
    """
    script = _Script(first, _ok())

    result = await run_with_one_retry(generate=script, shape=_FIRST_SHAPE)

    assert_that(script.shapes).is_length(1)
    assert_that(result.failed).is_true()
    assert_that(result.retried).is_false()
    (degradation,) = question_pass_degradations(questions=result)
    assert_that(degradation.detail).is_equal_to(str(result.failure_kind))


@pytest.mark.parametrize(
    "stop",
    [
        pytest.param(AICostBudgetExceededError("cap"), id="cost-cap"),
        pytest.param(AIProviderError(SIGTERM_TIMEOUT_MESSAGE), id="sigterm"),
    ],
)
@pytest.mark.parametrize("on_retry", [False, True], ids=["first", "retry"])
async def test_a_stop_propagates_on_either_attempt(
    stop: Exception,
    on_retry: bool,
) -> None:
    """The run's graceful halts are never swallowed, on either attempt.

    Args:
        stop: The stop raised by the attempt.
        on_retry: Whether the stop comes from the retry or the first call.
    """
    outcomes = (_failed(QuestionFailureKind.NOT_JSON), stop) if on_retry else (stop,)
    script = _Script(*outcomes)
    recorded: list[RunQuestions] = []

    with pytest.raises(type(stop)):
        await run_with_one_retry(
            generate=script,
            shape=_FIRST_SHAPE,
            record=recorded.append,
        )
    assert_that(script.shapes).is_length(2 if on_retry else 1)
    # A stop on the retry leaves the billed first attempt with the run (#2826);
    # a stop on the first call has nothing billed to keep.
    assert_that([r.usage.input_tokens for r in recorded]).is_equal_to(
        [10] if on_retry else [],
    )


async def test_the_first_attempt_is_recorded_before_a_stopped_retry() -> None:
    """``record`` receives the billed first attempt before the retry runs."""
    recorded: list[RunQuestions] = []
    script = _Script(
        _failed(QuestionFailureKind.NOT_JSON),
        AICostBudgetExceededError("cap"),
    )

    with pytest.raises(AICostBudgetExceededError):
        await run_with_one_retry(
            generate=script,
            shape=_FIRST_SHAPE,
            record=recorded.append,
        )

    assert_that(recorded).is_length(1)
    assert_that(recorded[0].failure_kind).is_equal_to(QuestionFailureKind.NOT_JSON)
    assert_that(recorded[0].usage.input_tokens).is_equal_to(10)


async def test_nothing_is_recorded_when_no_retry_follows() -> None:
    """A pass that succeeds, or fails without a retry, records nothing early."""
    recorded: list[RunQuestions] = []

    await run_with_one_retry(
        generate=_Script(_ok()),
        shape=_FIRST_SHAPE,
        record=recorded.append,
    )
    await run_with_one_retry(
        generate=_Script(_failed(QuestionFailureKind.NOT_LIST)),
        shape=_FIRST_SHAPE,
        record=recorded.append,
    )

    assert_that(recorded).is_empty()


async def test_a_turn_limit_capture_is_json_encoded() -> None:
    """The turn-limit capture follows the one-line JSON contract."""
    script = _Script(
        AITurnLimitError("turn limit", turns=12),
        AITurnLimitError("turn limit", turns=12),
    )

    result = await run_with_one_retry(generate=script, shape=_FIRST_SHAPE)

    assert_that(json.loads(result.capture)).is_equal_to("<turn limit: 12 turns>")


# --- the degradation record ---------------------------------------------------


def test_detail_is_serialized_only_when_set() -> None:
    """Every other degradation's payload is unchanged."""
    plain = CoverageDegradation(
        reason=CoverageDegradationReason.GENERATED_QUESTIONS_FAILED,
        chunk_index=-1,
        split=False,
    )
    detailed = CoverageDegradation(
        reason=CoverageDegradationReason.GENERATED_QUESTIONS_FAILED,
        chunk_index=-1,
        split=False,
        detail="not_list",
    )

    assert_that(plain.to_dict()).does_not_contain_key("detail")
    assert_that(detailed.to_dict()["detail"]).is_equal_to("not_list")


def _classifier() -> ModuleType:
    """Load the CI outcome classifier script.

    Returns:
        The classifier module.

    Raises:
        RuntimeError: When the module spec cannot be created.
    """
    script = REPO_ROOT / "scripts" / "ci" / "classify_review_outcome.py"
    spec = importlib.util.spec_from_file_location(
        "classify_review_outcome_2813",
        script,
    )
    if spec is None or spec.loader is None:
        msg = f"Unable to load {script}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    sys.modules["classify_review_outcome_2813"] = module
    spec.loader.exec_module(module)
    return module


def test_the_classifier_outcome_ignores_detail() -> None:
    """``detail`` is additive: the CI outcome and exit code do not change."""
    classifier = _classifier()

    def envelope(extra: dict[str, str]) -> str:
        degradation = {
            "reason": "generated_questions_failed",
            "chunk_index": -1,
            **extra,
        }
        return json.dumps(
            {
                "readiness_verdict": "ready",
                "findings_coverage_complete": False,
                "coverage_degradations": [degradation],
                "coverage": {
                    "reviewed": 1,
                    "carried": 0,
                    "awaiting": 0,
                    "invalidated": 0,
                    "eligible": 1,
                    "covered_at_head": 1,
                    "complete": True,
                },
            },
        )

    without = classifier.classify(status=0, output=envelope({}))
    with_detail = classifier.classify(
        status=0,
        output=envelope({"detail": "not_list; retried once"}),
    )

    assert_that(with_detail.outcome).is_equal_to(without.outcome)
    assert_that(with_detail.exit_code).is_equal_to(without.exit_code)


async def test_a_call_failed_warning_is_one_line() -> None:
    """Provider error text is JSON-encoded, so a ``::`` line stays inert."""
    messages: list[str] = []
    sink = logger.add(lambda message: messages.append(str(message)), level="WARNING")
    try:
        await run_with_one_retry(
            generate=_Script(AIProviderError("boom\n::error::injected")),
            shape=_FIRST_SHAPE,
        )
    finally:
        logger.remove(sink)

    failed = [line for line in messages if "question call failed" in line]
    assert_that(failed).is_length(1)
    assert_that(failed[0].rstrip("\n")).does_not_contain("\n")
    assert_that(failed[0]).contains("\\n::error::injected")

"""The once-per-run per-PR question pass (issue #2720, milestone 0 step 0.9).

The chunk prompt carries a short rubric plus questions written for *this*
change. The questions come from one provider call per run over the redacted
whole-PR diff (fitted to the synthesis budget), the PR title and the body;
every chunk shares them. These tests pin the fitting, the trimming note, the
parse ladder, the cap, the ``G<n>`` ids, and the run wiring: exactly one call
per run, its usage folded into the run, and a failure degrading the run to the
rubric alone instead of ending it.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.ai.budget import CostBudget
from lintro.ai.config import AIConfig
from lintro.ai.exceptions import (
    AICostBudgetExceededError,
    AIProviderError,
    AITurnLimitError,
)
from lintro.ai.providers.response import AIResponse
from lintro.ai.registry import AIProvider
from lintro.ai.review.enums.coverage_degradation_reason import (
    CoverageDegradationReason,
)
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.coverage_degradation import (
    SYNTHESIS_CHUNK_INDEX,
    CoverageDegradation,
)
from lintro.ai.review.models.pr_metadata import PRMetadata
from lintro.ai.review.models.review_chunk import ReviewChunk
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.orchestrator import run_review_async
from lintro.ai.review.question_pass import (
    MAX_QUESTION_CHARS,
    MAX_RUN_QUESTIONS,
    MAX_RUN_QUESTIONS_TOKENS,
    RunQuestions,
    fit_diff_to_budget,
    generate_run_questions,
    question_pass_degradations,
)
from lintro.ai.review.session import ReviewSessionOptions
from lintro.ai.token_budget import estimate_tokens

if TYPE_CHECKING:
    from pathlib import Path

    from lintro.ai.review.models.review_result import ReviewResult

pytestmark = pytest.mark.generated_questions

_FILE_A = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-a\n+aa\n"
_FILE_B = "diff --git a/b.py b/b.py\n--- a/b.py\n+++ b/b.py\n@@ -1 +1 @@\n-b\n+bb\n"
_FILE_C = "diff --git a/c.py b/c.py\n--- a/c.py\n+++ b/c.py\n@@ -1 +1 @@\n-c\n+cc\n"


def _response(*, content: str) -> AIResponse:
    """Wrap raw model text in a provider response.

    Args:
        content: The model's answer text.

    Returns:
        A response carrying token usage the run can fold in.
    """
    return AIResponse(
        content=content,
        model="claude-sonnet-4-6",
        provider=AIProvider.ANTHROPIC,
        input_tokens=10,
        output_tokens=20,
        cost_estimate=0.01,
    )


def _questions_payload(*questions: str) -> str:
    """Serialize a generated-questions answer.

    Args:
        *questions: The question texts, in order.

    Returns:
        The JSON the generator template asks for.
    """
    return json.dumps(
        {
            "generated_questions": [
                {"id": f"G{index}", "question": text, "rationale": "because"}
                for index, text in enumerate(questions, start=1)
            ],
        },
    )


def _context(*, files: tuple[str, ...] = ("a.py",)) -> ReviewContext:
    """Build a review context whose diff covers ``files``.

    Args:
        files: Changed file paths; the diff carries one hunk per file.

    Returns:
        The context, with PR metadata so the title and body reach the prompt.
    """
    sections = {"a.py": _FILE_A, "b.py": _FILE_B, "c.py": _FILE_C}
    return ReviewContext(
        base_ref="main",
        head_ref="feature",
        changed_files=[
            ChangedFile(path=path, status="modified", additions=1, deletions=1)
            for path in files
        ],
        unified_diff="".join(sections[path] for path in files),
        pr_metadata=PRMetadata(
            title="Rename the default",
            body="Callers must pass retries now.",
            number=1,
            repo="lgtm-hq/py-lintro",
        ),
    )


def _provider() -> MagicMock:
    """Return a provider double the run session can own and close.

    Returns:
        Configured mock provider.
    """
    provider = MagicMock()
    provider.aclose = AsyncMock()
    provider.model_name = "claude-sonnet-4-6"
    provider.name = "anthropic"
    provider.capabilities.supports_sessions = False
    return provider


async def _generate(*, content: str, diff_budget: int = 10_000) -> RunQuestions:
    """Run the generator against one canned answer.

    Args:
        content: The model's answer text.
        diff_budget: Token budget for the embedded diff.

    Returns:
        The generator's result.
    """
    with patch(
        "lintro.ai.review.provider_call.call_ai",
        new=AsyncMock(return_value=_response(content=content)),
    ):
        return await generate_run_questions(
            context=_context(),
            provider=_provider(),
            ai_config=AIConfig(enabled=True, review=True),
            budget=CostBudget(max_cost_usd=None),
            diff_budget=diff_budget,
        )


# --- fitting ---------------------------------------------------------------


def test_a_diff_within_budget_is_kept_whole() -> None:
    """Every file is kept when the whole diff fits."""
    diff, seen, total = fit_diff_to_budget(
        unified_diff=_FILE_A + _FILE_B,
        diff_budget=10_000,
    )

    assert_that(diff).is_equal_to(_FILE_A + _FILE_B)
    assert_that((seen, total)).is_equal_to((2, 2))


def test_files_are_kept_whole_in_path_order_until_one_does_not_fit() -> None:
    """The first file that does not fit ends the selection at a file edge."""
    budget = estimate_tokens(_FILE_A) + estimate_tokens(_FILE_B)

    diff, seen, total = fit_diff_to_budget(
        unified_diff=_FILE_C + _FILE_A + _FILE_B,
        diff_budget=budget,
    )

    assert_that(diff).is_equal_to(_FILE_A + _FILE_B)
    assert_that((seen, total)).is_equal_to((2, 3))
    assert_that(diff).does_not_contain("c.py")


def test_a_diff_that_fits_the_budget_exactly_is_kept_whole() -> None:
    """The fitter charges the concatenated text, not per-file roundings.

    Summing per-file estimates over-counts by up to one token a file; a diff
    whose whole-text estimate equals the budget must not be trimmed.
    """
    whole = _FILE_A + _FILE_B
    budget = estimate_tokens(whole)
    assert_that(estimate_tokens(_FILE_A) + estimate_tokens(_FILE_B)).is_greater_than(
        budget,
    )

    diff, seen, total = fit_diff_to_budget(unified_diff=whole, diff_budget=budget)

    assert_that(diff).is_equal_to(whole)
    assert_that((seen, total)).is_equal_to((2, 2))


def test_a_diff_with_no_file_headers_is_one_file_kept_or_dropped() -> None:
    """A diff the splitter cannot section counts as one file."""
    fits = fit_diff_to_budget(unified_diff="+x\n", diff_budget=10_000)
    dropped = fit_diff_to_budget(unified_diff="+x\n" * 100, diff_budget=1)
    empty = fit_diff_to_budget(unified_diff="", diff_budget=1)

    assert_that(fits).is_equal_to(("+x\n", 1, 1))
    assert_that(dropped).is_equal_to(("", 0, 1))
    assert_that(empty).is_equal_to(("", 0, 0))


@pytest.mark.parametrize(
    ("unified_diff", "expected_note"),
    [
        (
            _FILE_A + _FILE_B,
            "(trimmed to the first 0 of 2 changed files to fit the budget)",
        ),
        ("+x\n" * 100, "(trimmed to the first 0 of 1 changed files to fit the budget)"),
    ],
    ids=["sectioned", "headerless"],
)
async def test_a_diff_dropped_whole_is_reported_as_trimmed(
    unified_diff: str,
    expected_note: str,
) -> None:
    """A budget too small for any file still says the model saw none of it.

    Args:
        unified_diff: The PR diff, sectioned or not.
        expected_note: The trimming note the prompt must carry.
    """
    seam = AsyncMock(return_value=_response(content=_questions_payload("Q?")))
    context = _context(files=("a.py", "b.py"))
    context = ReviewContext(
        base_ref=context.base_ref,
        head_ref=context.head_ref,
        changed_files=context.changed_files,
        unified_diff=unified_diff,
        pr_metadata=context.pr_metadata,
    )

    with patch("lintro.ai.review.provider_call.call_ai", new=seam):
        questions = await generate_run_questions(
            context=context,
            provider=_provider(),
            ai_config=AIConfig(enabled=True, review=True),
            budget=CostBudget(max_cost_usd=None),
            diff_budget=1,
        )

    prompt = seam.call_args.kwargs["user_prompt"]
    assert_that(prompt).contains(expected_note)
    assert_that(prompt).does_not_contain("+aa")
    assert_that(questions.diff_trimmed).is_true()
    assert_that(questions.files_seen).is_equal_to(0)
    assert_that(questions.failed).is_false()


# --- the generator call ------------------------------------------------------


async def test_the_prompt_carries_title_body_files_and_diff() -> None:
    """The generator reads the PR, not one chunk."""
    seam = AsyncMock(return_value=_response(content=_questions_payload("Q?")))

    with patch("lintro.ai.review.provider_call.call_ai", new=seam):
        questions = await generate_run_questions(
            context=_context(files=("a.py", "b.py")),
            provider=_provider(),
            ai_config=AIConfig(enabled=True, review=True),
            budget=CostBudget(max_cost_usd=None),
            diff_budget=10_000,
        )

    assert_that(questions.lines).is_equal_to(("G1. Q?",))
    assert_that(questions.diff_trimmed).is_false()
    prompt = seam.call_args.kwargs["user_prompt"]
    assert_that(prompt).contains("Rename the default")
    assert_that(prompt).contains("Callers must pass retries now.")
    assert_that(prompt).contains("`a.py`", "`b.py`")
    assert_that(prompt).contains(_FILE_B.strip())
    assert_that(prompt).does_not_contain("trimmed to the first")


async def test_a_trimmed_diff_says_so_in_the_prompt_and_the_result() -> None:
    """When the diff does not fit, the model and the run record both learn it."""
    seam = AsyncMock(return_value=_response(content=_questions_payload("Q?")))

    with patch("lintro.ai.review.provider_call.call_ai", new=seam):
        questions = await generate_run_questions(
            context=_context(files=("a.py", "b.py", "c.py")),
            provider=_provider(),
            ai_config=AIConfig(enabled=True, review=True),
            budget=CostBudget(max_cost_usd=None),
            diff_budget=estimate_tokens(_FILE_A),
        )

    prompt = seam.call_args.kwargs["user_prompt"]
    assert_that(prompt).contains(
        "(trimmed to the first 1 of 3 changed files to fit the budget)",
    )
    assert_that(prompt).does_not_contain("c.py b/c.py")
    assert_that(questions.diff_trimmed).is_true()
    assert_that((questions.files_seen, questions.files_total)).is_equal_to((1, 3))
    assert_that(questions.failed).is_false()


async def test_questions_are_numbered_g1_onwards_and_capped() -> None:
    """Questions render as ``G<n>.`` lines and never exceed the cap."""
    texts = tuple(f"Question {index}?" for index in range(MAX_RUN_QUESTIONS + 5))

    questions = await _generate(content=_questions_payload(*texts))

    assert_that(questions.count).is_equal_to(MAX_RUN_QUESTIONS)
    assert_that(questions.lines).is_length(MAX_RUN_QUESTIONS)
    assert_that(questions.lines[0]).is_equal_to("G1. Question 0?")
    assert_that(questions.lines[-1]).is_equal_to(
        f"G{MAX_RUN_QUESTIONS}. Question {MAX_RUN_QUESTIONS - 1}?",
    )
    assert_that(questions.failed).is_false()


async def test_blank_and_malformed_items_are_skipped_without_gaps() -> None:
    """Ids stay consecutive across items the model got wrong."""
    payload = json.dumps(
        {
            "generated_questions": [
                {"question": "  "},
                "not an object",
                {"question": "Real one?"},
                {"rationale": "no question key"},
                {"question": "Another?"},
            ],
        },
    )

    questions = await _generate(content=payload)

    assert_that(questions.lines).is_equal_to(("G1. Real one?", "G2. Another?"))


async def test_each_question_is_one_bounded_line() -> None:
    """Multi-line and over-long answers cannot inflate the shared block."""
    long = "word " * 300
    questions = await _generate(
        content=_questions_payload("First line\nsecond   line\n\nthird", long),
    )

    assert_that(questions.lines).is_length(2)
    assert_that(questions.count).is_equal_to(2)
    assert_that(questions.lines[0]).is_equal_to("G1. First line second line third")
    assert_that(len(questions.lines[1])).is_less_than_or_equal_to(MAX_QUESTION_CHARS)
    assert_that(questions.lines[1]).starts_with("G2. word word")
    assert_that(questions.lines[1]).ends_with("…")
    assert_that(estimate_tokens(questions.text)).is_less_than_or_equal_to(
        MAX_RUN_QUESTIONS_TOKENS,
    )


async def test_ten_maximal_questions_fit_the_reserved_ceiling() -> None:
    """The ceiling covers ten full lines and their separators."""
    # Unbroken tokens force the hard cut, so every line is exactly maximal
    # and the block sits at the ceiling itself, separators included.
    questions = await _generate(
        content=_questions_payload(*(["x" * 2000] * MAX_RUN_QUESTIONS)),
    )

    assert_that(questions.lines).is_length(MAX_RUN_QUESTIONS)
    for line in questions.lines:
        assert_that(len(line)).is_equal_to(MAX_QUESTION_CHARS)
    assert_that(estimate_tokens(questions.text)).is_equal_to(
        MAX_RUN_QUESTIONS_TOKENS,
    )


async def test_an_unbroken_overlong_question_is_cut_hard() -> None:
    """One token with no word boundary is cut, not reduced to the prefix."""
    questions = await _generate(content=_questions_payload("x" * 2000))

    line = questions.lines[0]
    assert_that(len(line)).is_less_than_or_equal_to(MAX_QUESTION_CHARS)
    assert_that(line).starts_with("G1. xxxx")
    assert_that(line).ends_with("…")
    assert_that(len(line)).is_greater_than(100)


def test_the_questions_ceiling_is_reserved_in_the_prompt_overhead() -> None:
    """Chunking leaves room for the block every chunk prompt will carry."""
    from lintro.ai.review.prompts import estimate_prompt_overhead

    overhead = estimate_prompt_overhead(
        context=_context(),
        checklist_text="",
        classifications=[],
        lint_results=None,
    )

    from lintro.ai.review.prompts import _PROMPT_OVERHEAD_TOKENS

    # Reserved on top of the floored estimate, never swallowed by the floor.
    assert_that(overhead).is_equal_to(
        _PROMPT_OVERHEAD_TOKENS + MAX_RUN_QUESTIONS_TOKENS,
    )


async def test_a_turn_limited_call_keeps_its_billed_usage(tmp_path: Path) -> None:
    """A CLI call stopped at the turn limit was billed; the failed pass says so.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    seam = _scripted_seam(
        AITurnLimitError(
            "Claude CLI stopped at the per-call turn limit (12 turns)",
            input_tokens=700,
            output_tokens=30,
            cost_estimate=0.07,
        ),
        _main_pass_response(),
        _main_pass_response(),
    )

    result = await _run(tmp_path=tmp_path, call_ai=seam)

    assert_that(result.metadata.partial).is_false()
    assert_that(
        [item.reason for item in result.metadata.coverage_degradations],
    ).is_equal_to([CoverageDegradationReason.GENERATED_QUESTIONS_FAILED])
    # 700 (the billed question call) + 10 + 10.
    assert_that(result.metadata.token_usage["prompt"]).is_equal_to(720)
    assert_that(result.metadata.cost_estimate_usd).is_close_to(0.09, 1e-9)


async def test_fenced_json_is_accepted() -> None:
    """A code-fenced answer parses like a bare one."""
    questions = await _generate(content=f"```json\n{_questions_payload('Q?')}\n```")

    assert_that(questions.lines).is_equal_to(("G1. Q?",))


@pytest.mark.parametrize(
    "content",
    [
        "not json at all",
        json.dumps({"findings": []}),
        json.dumps({"generated_questions": "none"}),
        json.dumps({"generated_questions": None}),
        json.dumps(["G1"]),
        json.dumps({"generated_questions": []}),
        json.dumps({"generated_questions": [{"question": " "}, "not an object"]}),
    ],
)
async def test_an_unusable_answer_is_a_failed_pass_that_keeps_its_usage(
    content: str,
) -> None:
    """The run reviews with the rubric alone but still pays for the call.

    Args:
        content: The unusable answer text.
    """
    questions = await _generate(content=content)

    assert_that(questions.failed).is_true()
    assert_that(questions.text).is_empty()
    assert_that(questions.count).is_equal_to(0)
    assert_that(questions.usage.input_tokens).is_equal_to(10)
    assert_that(questions.usage.cost_estimate).is_equal_to(0.01)


# --- degradation --------------------------------------------------------------


def test_a_failed_pass_is_recorded_once_as_a_whole_run_degradation() -> None:
    """The degradation carries the synthesis sentinel, not a chunk index."""
    assert_that(
        question_pass_degradations(questions=RunQuestions(failed=True)),
    ).is_equal_to(
        (
            CoverageDegradation(
                reason=CoverageDegradationReason.GENERATED_QUESTIONS_FAILED,
                chunk_index=SYNTHESIS_CHUNK_INDEX,
                split=False,
            ),
        ),
    )


@pytest.mark.parametrize(
    "questions",
    [None, RunQuestions(), RunQuestions(text="G1. Q?", count=1)],
    ids=["not-run", "disabled", "generated"],
)
def test_a_pass_that_did_not_fail_records_nothing(
    questions: RunQuestions | None,
) -> None:
    """Only a failed pass degrades the run.

    Args:
        questions: The pass result, or ``None`` when it did not run.
    """
    assert_that(question_pass_degradations(questions=questions)).is_empty()


# --- run wiring --------------------------------------------------------------


def _chunk(*, path: str) -> ReviewChunk:
    """Build a one-file chunk numbered like ``resolve_review_chunks`` would.

    Args:
        path: The chunk's file.

    Returns:
        The chunk.
    """
    sections = {"a.py": _FILE_A, "b.py": _FILE_B}
    return ReviewChunk(
        id=list(sections).index(path) + 1,
        files=[path],
        diff=sections[path],
        relationship="single-file",
    )


def _main_pass_response() -> AIResponse:
    """Return a main-pass answer with no findings.

    Returns:
        A parseable chunk review response.
    """
    return _response(content=json.dumps({"findings": [], "flagged_files": []}))


async def _run(
    *,
    tmp_path: Path,
    call_ai: AsyncMock,
    generated_questions: bool = True,
) -> ReviewResult:
    """Drive a two-chunk review with a scripted provider seam.

    Args:
        tmp_path: Temporary directory used as the repository root.
        call_ai: The patched ``provider_call.call_ai`` double.
        generated_questions: The ``review_generated_questions`` knob.

    Returns:
        The finished review result.
    """
    context = _context(files=("a.py", "b.py"))
    context = ReviewContext(
        base_ref=context.base_ref,
        head_ref=context.head_ref,
        changed_files=context.changed_files,
        unified_diff=context.unified_diff,
        pr_metadata=context.pr_metadata,
        repo_root=str(tmp_path),
    )
    with (
        patch(
            "lintro.ai.review.run_planning.resolve_review_chunks",
            return_value=[_chunk(path="a.py"), _chunk(path="b.py")],
        ),
        patch("lintro.ai.review.provider_call.call_ai", new=call_ai),
    ):
        return await run_review_async(
            context=context,
            options=ReviewSessionOptions(
                provider=_provider(),
                ai_config=AIConfig(
                    enabled=True,
                    review=True,
                    review_generated_questions=generated_questions,
                ),
                depth=1,
                checklist_items=[],
                checklist_text="",
                classifications=[],
            ),
        )


def _scripted_seam(*answers: AIResponse | Exception) -> AsyncMock:
    """Script the seam's answers in call order.

    Args:
        *answers: Responses to return, or exceptions to raise, in order.

    Returns:
        The seam; ``call_args_list`` records every call's keywords.
    """
    return AsyncMock(side_effect=list(answers))


def _prompts(seam: AsyncMock) -> list[str]:
    """Return the user prompt of every call the seam answered, in order.

    Args:
        seam: The scripted seam.

    Returns:
        The user prompts.
    """
    return [call.kwargs["user_prompt"] for call in seam.call_args_list]


async def test_the_pass_runs_once_per_run_and_every_chunk_shares_it(
    tmp_path: Path,
) -> None:
    """Two chunks make three calls: one question call, then one review each.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    seam = _scripted_seam(
        _response(content=_questions_payload("Does b.py still call a?")),
        _main_pass_response(),
        _main_pass_response(),
    )

    result = await _run(tmp_path=tmp_path, call_ai=seam)

    prompts = _prompts(seam)
    assert_that(prompts).is_length(3)
    assert_that(prompts[0]).contains("Rename the default")
    # The pass never reuses the review's durable session (#2720).
    assert_that(seam.call_args_list[0].kwargs["use_one_shot"]).is_true()
    for chunk_prompt in prompts[1:]:
        # Model-produced text is fenced by the prompt's own boundary marker,
        # with the trusted heading outside the fence.
        marker = chunk_prompt.split("<")[1].split(">")[0]
        assert_that(chunk_prompt).contains(
            "### Questions for this change (consider each; do not answer them)\n\n"
            f"<{marker}>\nG1. Does b.py still call a?\n</{marker}>\n",
        )
    assert_that(result.metadata.generated_questions).is_equal_to(
        ("G1. Does b.py still call a?",),
    )
    assert_that(result.metadata.questions_diff_trimmed).is_false()
    assert_that(result.metadata.coverage_degradations).is_empty()
    assert_that(result.metadata.partial).is_false()
    # 10 + 10 + 10 prompt tokens: the pass is charged once, not per chunk.
    assert_that(result.metadata.token_usage["prompt"]).is_equal_to(30)


async def test_the_knob_off_makes_no_extra_call_and_renders_the_placeholder(
    tmp_path: Path,
) -> None:
    """Disabling the pass reviews with the rubric alone and no degradation.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    seam = _scripted_seam(_main_pass_response(), _main_pass_response())

    result = await _run(tmp_path=tmp_path, call_ai=seam, generated_questions=False)

    prompts = _prompts(seam)
    assert_that(prompts).is_length(2)
    assert_that(prompts[0]).contains(
        "(no questions were generated for this change; review against the rubric)",
    )
    assert_that(result.metadata.generated_questions).is_empty()
    assert_that(result.metadata.coverage_degradations).is_empty()


async def test_a_failed_pass_degrades_the_run_instead_of_ending_it(
    tmp_path: Path,
) -> None:
    """A provider error on the question call still reviews every chunk.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    seam = _scripted_seam(
        AIProviderError("question generator timed out"),
        _main_pass_response(),
        _main_pass_response(),
    )

    result = await _run(tmp_path=tmp_path, call_ai=seam)

    assert_that(seam.call_count).is_equal_to(3)
    assert_that(result.metadata.partial).is_false()
    assert_that(result.metadata.chunks_reviewed).is_equal_to(2)
    assert_that(result.metadata.generated_questions).is_empty()
    assert_that(
        [
            (item.reason, item.chunk_index)
            for item in result.metadata.coverage_degradations
        ],
    ).is_equal_to(
        [(CoverageDegradationReason.GENERATED_QUESTIONS_FAILED, SYNTHESIS_CHUNK_INDEX)],
    )


async def test_a_cost_cap_stop_on_the_question_call_ends_the_run_as_partial(
    tmp_path: Path,
) -> None:
    """The cap is the run's graceful halt, never a "failed pass" note.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    seam = _scripted_seam(AICostBudgetExceededError("cost cap reached"))

    result = await _run(tmp_path=tmp_path, call_ai=seam)

    assert_that(seam.call_count).is_equal_to(1)
    assert_that(result.metadata.partial).is_true()
    assert_that(result.metadata.stopped_reason).contains("cost cap")
    assert_that(result.metadata.chunks_reviewed).is_equal_to(0)
    assert_that(result.metadata.coverage_degradations).is_empty()


async def test_a_run_stopped_after_the_pass_still_reports_it(
    tmp_path: Path,
) -> None:
    """A cost cap tripping in the fan-out keeps the questions and their usage.

    The pass ran and was paid for before the stop; the partial run must carry
    its questions, charge its tokens, and keep a failed pass's degradation.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    seam = _scripted_seam(
        _response(content=_questions_payload("Q?")),
        _main_pass_response(),
        AICostBudgetExceededError("cost cap reached"),
    )

    result = await _run(tmp_path=tmp_path, call_ai=seam)

    assert_that(result.metadata.partial).is_true()
    assert_that(result.metadata.chunks_reviewed).is_equal_to(1)
    assert_that(result.metadata.generated_questions).is_equal_to(("G1. Q?",))
    # 10 (question call) + 10 (the one completed chunk).
    assert_that(result.metadata.token_usage["prompt"]).is_equal_to(20)

    failed = _scripted_seam(
        AIProviderError("question generator timed out"),
        _main_pass_response(),
        AICostBudgetExceededError("cost cap reached"),
    )

    stopped = await _run(tmp_path=tmp_path, call_ai=failed)

    assert_that(stopped.metadata.partial).is_true()
    assert_that(
        [item.reason for item in stopped.metadata.coverage_degradations],
    ).is_equal_to([CoverageDegradationReason.GENERATED_QUESTIONS_FAILED])


async def test_a_run_stopped_before_any_chunk_still_charges_the_pass(
    tmp_path: Path,
) -> None:
    """With zero partials the pass is still charged and its failure recorded.

    The usage and the degradation come from the run outcome, not from a chunk
    partial, so a cost cap tripping on the first chunk call loses neither.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    seam = _scripted_seam(
        _response(content=_questions_payload("Q?")),
        AICostBudgetExceededError("cost cap reached"),
    )

    result = await _run(tmp_path=tmp_path, call_ai=seam)

    assert_that(result.metadata.partial).is_true()
    assert_that(result.metadata.chunks_reviewed).is_equal_to(0)
    assert_that(result.metadata.generated_questions).is_equal_to(("G1. Q?",))
    assert_that(result.metadata.token_usage["prompt"]).is_equal_to(10)
    assert_that(result.metadata.cost_estimate_usd).is_equal_to(0.01)

    failed = _scripted_seam(
        AIProviderError("question generator timed out"),
        AICostBudgetExceededError("cost cap reached"),
    )

    stopped = await _run(tmp_path=tmp_path, call_ai=failed)

    assert_that(stopped.metadata.chunks_reviewed).is_equal_to(0)
    assert_that(
        [item.reason for item in stopped.metadata.coverage_degradations],
    ).is_equal_to([CoverageDegradationReason.GENERATED_QUESTIONS_FAILED])


async def test_an_interrupt_during_the_question_call_stops_the_run() -> None:
    """SIGTERM while the question call is in flight ends the run promptly.

    The call is raced against the run's stop event like every other provider
    call; the winner is the persistable SIGTERM timeout, so the orchestrator
    takes its stopped-run path instead of waiting out the provider timeout.
    """
    stop = asyncio.Event()
    entered = asyncio.Event()

    async def _hang(**_kwargs: object) -> AIResponse:
        """Signal entry, then block until cancelled.

        Args:
            **_kwargs: Provider-call keywords the seam ignores.

        Returns:
            Never; the call is cancelled by the race.
        """
        entered.set()
        await asyncio.sleep(3600)
        return _response(content="")

    async def _fire_stop() -> None:
        """Set the stop event once the provider call is in flight."""
        await entered.wait()
        stop.set()

    with patch(
        "lintro.ai.review.provider_call.call_ai",
        new=AsyncMock(side_effect=_hang),
    ):
        firing = asyncio.ensure_future(_fire_stop())
        try:
            with pytest.raises(AIProviderError, match="SIGTERM"):
                await asyncio.wait_for(
                    generate_run_questions(
                        context=_context(),
                        provider=_provider(),
                        ai_config=AIConfig(enabled=True, review=True),
                        budget=CostBudget(max_cost_usd=None),
                        diff_budget=10_000,
                        stop=stop,
                    ),
                    timeout=5,
                )
        finally:
            await firing

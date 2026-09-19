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

import json
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.ai.budget import CostBudget
from lintro.ai.config import AIConfig
from lintro.ai.exceptions import AICostBudgetExceededError, AIProviderError
from lintro.ai.providers.response import AIResponse
from lintro.ai.registry import AIProvider
from lintro.ai.review.enums.coverage_degradation_reason import (
    CoverageDegradationReason,
)
from lintro.ai.review.merge import ChunkReviewPartial
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
    MAX_RUN_QUESTIONS,
    RunQuestions,
    fit_diff_to_budget,
    fold_question_pass,
    generate_run_questions,
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


def test_a_diff_with_no_file_headers_is_all_or_nothing() -> None:
    """A diff the splitter cannot section is embedded whole or dropped."""
    fits = fit_diff_to_budget(unified_diff="+x\n", diff_budget=10_000)
    dropped = fit_diff_to_budget(unified_diff="+x\n" * 100, diff_budget=1)

    assert_that(fits).is_equal_to(("+x\n", 0, 0))
    assert_that(dropped).is_equal_to(("", 0, 0))


# --- the generator call ------------------------------------------------------


async def test_the_prompt_carries_title_body_files_and_diff() -> None:
    """The generator reads the PR, not one chunk."""
    seam = AsyncMock(return_value=_response(content=_questions_payload("Q?")))

    with patch("lintro.ai.review.provider_call.call_ai", new=seam):
        await generate_run_questions(
            context=_context(files=("a.py", "b.py")),
            provider=_provider(),
            ai_config=AIConfig(enabled=True, review=True),
            budget=CostBudget(max_cost_usd=None),
            diff_budget=10_000,
        )

    prompt = seam.call_args.kwargs["user_prompt"]
    assert_that(prompt).contains("Rename the default")
    assert_that(prompt).contains("Callers must pass retries now.")
    assert_that(prompt).contains("`a.py`", "`b.py`")
    assert_that(prompt).contains(_FILE_B.strip())
    assert_that(prompt).does_not_contain("trimmed to the first")
    assert_that(seam.call_args.kwargs["use_one_shot"]).is_false()


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


# --- folding -----------------------------------------------------------------


def _partial(*, input_tokens: int = 100) -> ChunkReviewPartial:
    """Build an empty chunk partial with known usage.

    Args:
        input_tokens: Prompt tokens the partial reports.

    Returns:
        The partial.
    """
    return ChunkReviewPartial(
        findings=(),
        input_tokens=input_tokens,
        output_tokens=5,
        cost_estimate=0.5,
    )


def test_usage_is_charged_to_the_first_partial_only() -> None:
    """The pass's tokens land on one partial so run totals count them once."""
    questions = RunQuestions(
        text="G1. Q?",
        count=1,
        usage=ChunkReviewPartial(
            findings=(),
            input_tokens=10,
            output_tokens=20,
            cost_estimate=0.01,
        ),
    )

    folded = fold_question_pass(
        partials=[_partial(input_tokens=100), _partial(input_tokens=200)],
        questions=questions,
    )

    assert_that([item.input_tokens for item in folded]).is_equal_to([110, 200])
    assert_that(folded[0].output_tokens).is_equal_to(25)
    assert_that(folded[0].cost_estimate).is_equal_to(0.51)
    assert_that(folded[0].coverage_degradations).is_empty()
    assert_that(folded[1]).is_equal_to(_partial(input_tokens=200))


def test_a_failed_pass_is_recorded_once_as_a_whole_run_degradation() -> None:
    """The degradation carries the synthesis sentinel, not a chunk index."""
    folded = fold_question_pass(
        partials=[_partial(), _partial()],
        questions=RunQuestions(failed=True),
    )

    assert_that(folded[0].coverage_degradations).is_equal_to(
        (
            CoverageDegradation(
                reason=CoverageDegradationReason.GENERATED_QUESTIONS_FAILED,
                chunk_index=SYNTHESIS_CHUNK_INDEX,
                split=False,
            ),
        ),
    )
    assert_that(folded[1].coverage_degradations).is_empty()


def test_folding_into_no_partials_is_a_no_op() -> None:
    """A run that reviewed nothing has nowhere to charge the pass."""
    assert_that(
        fold_question_pass(partials=[], questions=RunQuestions(failed=True)),
    ).is_empty()


# --- run wiring --------------------------------------------------------------


def _chunk(*, path: str) -> ReviewChunk:
    """Build a one-file chunk.

    Args:
        path: The chunk's file.

    Returns:
        The chunk.
    """
    sections = {"a.py": _FILE_A, "b.py": _FILE_B}
    return ReviewChunk(
        id=1,
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
    for chunk_prompt in prompts[1:]:
        assert_that(chunk_prompt).contains(
            "### Questions for this change",
            "G1. Does b.py still call a?",
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

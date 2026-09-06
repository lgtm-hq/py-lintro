"""End-to-end goldens for ``run_review`` and its metadata (issue #2298).

The provider is replaced at ``call_ai`` — the single seam every provider call
goes through — and the two fixed payload files are replayed. Everything below
``call_ai`` is untouched, so these goldens characterise the orchestrator, not
a provider client.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from assertpy import assert_that

from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport
from lintro.ai.providers.capabilities import ProviderCapabilities
from lintro.ai.providers.response import AIResponse
from lintro.ai.review.models.review_metadata import ReviewMetadata
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.orchestrator import (
    resolve_review_chunks,
    run_review,
)
from lintro.ai.review.timings import ReviewTimings
from tests.unit.ai.review.golden.golden_fixtures import (
    GOLDEN_BOUNDARY,
    GOLDEN_RESPONSES,
    RENAMED_FROM,
    golden_checklist_items,
    golden_checklist_text,
    golden_chunks,
    golden_classifications,
    golden_review_context,
)
from tests.unit.ai.review.golden.golden_io import (
    SNAPSHOT_DIR,
    assert_golden_json,
    dump_json,
    load_payload,
    to_jsonable,
)

#: Provider identity used by the goldens. Deliberately not a real provider:
#: lintro has no default provider and the goldens must not imply one.
GOLDEN_PROVIDER: str = "golden-provider"
GOLDEN_MODEL: str = "golden-model"

#: Metadata fields whose values are wall-clock or run-time dependent, mapped to
#: the sentinel each is replaced with before serialisation. This mapping is the
#: single source of truth: :func:`_normalize_metadata` applies it and
#: ``test_run_review_metadata_volatile_fields_keep_their_types`` asserts the
#: names still exist, so a new volatile field cannot be sentinelled in one place
#: and forgotten in the other.
VOLATILE_METADATA_SENTINELS: dict[str, Any] = {
    "timestamp": "<timestamp>",
    "duration_seconds": -1.0,
    "phase_timings": {},
    "timings": None,
}


def _stub_provider() -> MagicMock:
    """Build a provider stub that never answers a call itself.

    Returns:
        A provider whose identity is stable and whose ``complete`` is unused
        because ``call_ai`` is replaced.
    """
    provider = MagicMock()
    provider.name = GOLDEN_PROVIDER
    provider.model_name = GOLDEN_MODEL
    provider.capabilities = ProviderCapabilities(supports_sessions=False)
    return provider


async def _replay_call_ai(*, user_prompt: str, budget: Any = None, **_: Any) -> Any:
    """Replay a fixed payload file based on which chunk the prompt covers.

    Args:
        user_prompt: Prompt the orchestrator built for this chunk.
        budget: Cost budget the orchestrator threads through ``call_ai``.
        **_: Remaining ``call_ai`` keyword arguments, unused by the replay.

    Returns:
        The fixed provider response for the matching chunk.
    """
    # The rename's *source* path appears only inside chunk 2's diff body; the
    # renamed path itself also appears in chunk 1's whole-PR file list, so it
    # cannot discriminate the chunks.
    is_second_chunk = RENAMED_FROM in user_prompt
    name, input_tokens, output_tokens, cost = GOLDEN_RESPONSES[
        1 if is_second_chunk else 0
    ]
    response = AIResponse(
        content=dump_json(value=load_payload(name=name)),
        model=GOLDEN_MODEL,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_estimate=cost,
        provider=GOLDEN_PROVIDER,
    )
    if budget is not None:
        budget.record(response.cost_estimate)
    return response


def _run_golden_review() -> ReviewResult:
    """Run the fixed review with the chunk plan and provider seam pinned.

    Returns:
        The complete review result for the fixed context.
    """
    with (
        patch(
            "lintro.ai.review.checklist_pass.make_boundary_marker",
            return_value=GOLDEN_BOUNDARY,
        ),
        patch(
            "lintro.ai.review.adversarial_pass.make_boundary_marker",
            return_value=GOLDEN_BOUNDARY,
        ),
        patch(
            "lintro.ai.review.prompts.make_boundary_marker",
            return_value=GOLDEN_BOUNDARY,
        ),
        patch(
            "lintro.ai.review.orchestrator.resolve_review_chunks",
            return_value=golden_chunks(),
        ),
        patch(
            "lintro.ai.review.response_pipeline.call_ai",
            side_effect=_replay_call_ai,
        ),
    ):
        return run_review(
            golden_review_context(),
            provider=_stub_provider(),
            ai_config=AIConfig(
                enabled=True,
                review=True,
                transport=AITransport.API,
                # Serialised so the replay order, and therefore the merged
                # finding order, is deterministic.
                max_parallel_calls=1,
            ),
            depth=1,
            checklist_items=golden_checklist_items(),
            checklist_text=golden_checklist_text(),
            classifications=golden_classifications(),
            context_window_override=200_000,
            workspace_root=Path("/workspace/py-lintro"),
        )


def _normalize_metadata(*, metadata: ReviewMetadata) -> ReviewMetadata:
    """Replace wall-clock metadata fields with sentinels.

    Args:
        metadata: Metadata produced by the golden run.

    Returns:
        Metadata whose volatile fields carry fixed placeholder values.
    """
    replacements = {
        **VOLATILE_METADATA_SENTINELS,
        # The keys are run-dependent, so only the values can be sentinelled.
        "phase_timings": dict.fromkeys(sorted(metadata.phase_timings), -1.0),
    }
    return replace(metadata, **replacements)


def test_run_review_result_matches_golden() -> None:
    """The full review result for the fixed context is byte-stable."""
    result = _run_golden_review()
    normalized = replace(
        result,
        metadata=_normalize_metadata(metadata=result.metadata),
    )

    assert_golden_json(name="run_review_result.golden", value=normalized)


def test_run_review_metadata_matches_golden() -> None:
    """Review metadata finalisation is pinned field by field."""
    result = _run_golden_review()

    assert_golden_json(
        name="run_review_metadata.golden",
        value=_normalize_metadata(metadata=result.metadata),
    )


def test_run_review_metadata_volatile_fields_keep_their_types() -> None:
    """Fields excluded from the golden are still asserted for shape.

    Sentinelling a field must not become a way to stop characterising it.
    """
    metadata = _run_golden_review().metadata

    assert_that(metadata.timestamp).is_not_empty()
    assert_that(metadata.duration_seconds).is_instance_of(float)
    assert_that(metadata.phase_timings).is_instance_of(dict)
    assert_that(metadata.timings).is_instance_of(ReviewTimings)
    assert_that(set(VOLATILE_METADATA_SENTINELS)).is_subset_of(
        set(ReviewMetadata.__dataclass_fields__),
    )


def test_resolve_review_chunks_plan_matches_golden() -> None:
    """Chunk planning over the fixed context is pinned at two budgets.

    The single-chunk fast path and the semantic path are both recorded so a
    later planner extraction cannot quietly change either.
    """
    context = golden_review_context()
    plans = {
        "fast_path": resolve_review_chunks(
            context=context,
            diff_budget=100_000,
            classifications=golden_classifications(),
        ),
        "forced_semantic": resolve_review_chunks(
            context=context,
            diff_budget=100_000,
            classifications=golden_classifications(),
            force_semantic_chunking=True,
        ),
    }

    assert_golden_json(name="chunk_plan.golden", value=plans)


def test_run_review_findings_match_the_merge_golden() -> None:
    """The replayed run and the direct merge agree on the merged findings.

    ``_replay_call_ai`` picks a payload by looking for the rename source path
    in the prompt. That is a substring discriminator, so this test is the loud
    failure mode for it: replaying one payload twice collapses the merge and
    this comparison breaks, which is exactly how the original mis-keyed replay
    was caught. It also keeps the two golden modules describing one run.
    """
    result = _run_golden_review()
    merged = json.loads(
        (SNAPSHOT_DIR / "merged_review_result.golden").read_text(encoding="utf-8"),
    )

    assert_that(to_jsonable(result.findings)).is_equal_to(merged["findings"])
    assert_that(to_jsonable(result.checklist)).is_equal_to(merged["checklist"])

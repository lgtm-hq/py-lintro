"""Characterization tests for the review session options object (#2301).

``run_review`` is the public facade and takes exactly two arguments: the
context and one :class:`ReviewSessionOptions`. The final slice of #2301
collapsed the run's settings onto that single surface — the facade no longer
declares a keyword (or a default) of its own — so what a caller who omits a
setting gets is decided in exactly one place. These tests pin that: the facade
forwards the object unchanged, and every setting a run has is a field on it.
"""

from __future__ import annotations

import asyncio
import dataclasses
import inspect
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from assertpy import assert_that

from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport
from lintro.ai.review import orchestrator
from lintro.ai.review.custom_agents import CustomAgentSpec
from lintro.ai.review.enums.review_category import ReviewCategory
from lintro.ai.review.enums.review_strictness import ReviewStrictness
from lintro.ai.review.models.checklist_item import ChecklistItem
from lintro.ai.review.models.file_classification import FileClassification
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.models.review_finding import Severity
from lintro.ai.review.models.review_metadata import ReviewMetadata
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.orchestrator import run_review
from lintro.ai.review.progress import NullReviewProgress
from lintro.ai.review.sensitivity import resolve_sensitivity_policy
from lintro.ai.review.session import ReviewSessionOptions
from lintro.config.review_config import ReviewSynthesisConfig
from tests.unit.ai.conftest import MockAIProvider

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


#: The value a caller gets for every optional run setting it omits. This is
#: the whole defaulted surface, not a sample: #2301 collapsed the defaults onto
#: this object alone, so a changed or newly defaulted field is a change to what
#: every adapter runs with and must be made here deliberately.
EXPECTED_SESSION_DEFAULTS: dict[str, object] = {
    "depth": 1,
    "context_window_override": None,
    "lint_results": None,
    "progress": None,
    "sensitivity": None,
    "force_semantic_chunking": False,
    "timeout": None,
    "custom_agents": (),
    "run_builtin_checklist": True,
    "workspace_root": None,
    "context_collection_seconds": 0.0,
    "prior_state": None,
    "force_full": False,
    "enforce_cost_cap": True,
    "stop": None,
    "synthesis": None,
}

#: Fields a caller must supply. Every other field is in
#: :data:`EXPECTED_SESSION_DEFAULTS`, and the two together are the class.
REQUIRED_SESSION_FIELDS: tuple[str, ...] = (
    "provider",
    "ai_config",
    "checklist_items",
    "checklist_text",
    "classifications",
)


def _session_option_defaults() -> dict[str, object]:
    """Read what a caller who supplies only the required fields actually gets.

    Built by constructing the object rather than by reading field metadata, so
    a ``default_factory`` field or a value normalized in ``__post_init__`` is
    reported as the caller sees it instead of being skipped.

    Returns:
        Mapping of field name to the value the instance carries, for every
        field outside :data:`REQUIRED_SESSION_FIELDS`.
    """
    options = ReviewSessionOptions(
        provider=MockAIProvider(),
        ai_config=AIConfig(enabled=True, transport=AITransport.API),
        checklist_items=[],
        checklist_text="",
        classifications=[],
    )
    return {
        field.name: getattr(options, field.name)
        for field in dataclasses.fields(ReviewSessionOptions)
        if field.name not in REQUIRED_SESSION_FIELDS
    }


def test_run_review_declares_no_defaults_of_its_own() -> None:
    """The facade takes the context and the options object, and nothing else.

    A keyword re-declared here would reintroduce the second default surface
    #2301 removed: a caller omitting it would get the facade's value, not the
    one :class:`ReviewSessionOptions` documents.
    """
    parameters = inspect.signature(run_review).parameters

    assert_that(list(parameters)).is_equal_to(["context", "options"])
    assert_that(parameters["options"].kind).is_equal_to(
        inspect.Parameter.KEYWORD_ONLY,
    )
    assert_that(parameters["options"].default).is_equal_to(
        inspect.Parameter.empty,
    )


def test_the_options_object_carries_the_run_defaults() -> None:
    """The defaulted surface is exactly the snapshot, value for value.

    Pinning the whole set rather than four of its members means a default that
    is silently added, removed, or retuned fails here instead of changing what
    every review runs with unnoticed.
    """
    defaults = _session_option_defaults()

    assert_that(defaults).is_equal_to(EXPECTED_SESSION_DEFAULTS)


def test_every_option_is_either_required_or_defaulted() -> None:
    """The required fields and the defaulted snapshot together are the class."""
    names = tuple(field.name for field in dataclasses.fields(ReviewSessionOptions))

    assert_that(sorted(names)).is_equal_to(
        sorted([*REQUIRED_SESSION_FIELDS, *EXPECTED_SESSION_DEFAULTS]),
    )


def _capturing_run_review_async(
    captured: list[ReviewSessionOptions],
) -> Callable[..., Awaitable[ReviewResult]]:
    """Build a stand-in for ``run_review_async`` that records its options.

    Args:
        captured: List the stand-in appends the received options object to.

    Returns:
        An awaitable stand-in with ``run_review_async``'s call shape that
        returns an empty result.
    """

    async def _run(
        context: ReviewContext,
        *,
        options: ReviewSessionOptions,
    ) -> ReviewResult:
        captured.append(options)
        return ReviewResult(
            metadata=ReviewMetadata(
                model="",
                provider="",
                context_window=0,
                depth=0,
                chunks_total=0,
                chunks_current=0,
                files_reviewed=0,
                files_total=0,
                checklist_items=0,
            ),
            summary="",
            checklist=(),
            findings=(),
        )

    return _run


def test_run_review_forwards_the_options_object_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The options the caller built are the options ``run_review_async`` gets.

    Every field is given a value distinct from its default so a facade that
    rebuilt (rather than forwarded) the object could not pass by coincidence.

    Args:
        monkeypatch: Pytest fixture used to swap in the capturing stand-in.
    """
    captured: list[ReviewSessionOptions] = []
    monkeypatch.setattr(
        orchestrator,
        "run_review_async",
        _capturing_run_review_async(captured),
    )
    provider = MockAIProvider()
    ai_config = AIConfig(
        enabled=True,
        transport=AITransport.API,
        max_parallel_calls=1,
    )
    checklist_items = [
        ChecklistItem(
            id=7,
            question="Packed?",
            domains=(),
            languages=(),
            category=ReviewCategory.SECURITY,
            tier=1,
        ),
    ]
    classifications = [FileClassification(path="a.py", domains=[])]
    progress = NullReviewProgress()
    sensitivity = resolve_sensitivity_policy(strictness=ReviewStrictness.THOROUGH)
    custom_agents = (
        CustomAgentSpec(
            name="packing",
            description="Packing agent.",
            include=("*.py",),
            exclude=(),
            severity=Severity.P2,
            strictness=ReviewStrictness.THOROUGH,
            model=None,
            enabled=True,
            body="Check packing.",
            path=Path("/tmp/packing.md"),
        ),
    )
    workspace_root = Path("/tmp/workspace")
    prior_state = ReviewState()
    stop = asyncio.Event()
    synthesis = ReviewSynthesisConfig(enabled=True)

    options = ReviewSessionOptions(
        provider=provider,
        ai_config=ai_config,
        depth=3,
        checklist_items=checklist_items,
        checklist_text="1. [security] Packed?",
        classifications=classifications,
        context_window_override=4242,
        lint_results="lint digest",
        progress=progress,
        sensitivity=sensitivity,
        force_semantic_chunking=True,
        timeout=12.5,
        custom_agents=custom_agents,
        run_builtin_checklist=False,
        workspace_root=workspace_root,
        context_collection_seconds=1.5,
        prior_state=prior_state,
        force_full=True,
        enforce_cost_cap=False,
        stop=stop,
        synthesis=synthesis,
    )

    orchestrator.run_review(
        ReviewContext(
            base_ref="base",
            head_ref="head",
            changed_files=[],
            unified_diff="",
        ),
        options=options,
    )

    expected: dict[str, object] = {
        "provider": provider,
        "ai_config": ai_config,
        "depth": 3,
        "checklist_items": checklist_items,
        "checklist_text": "1. [security] Packed?",
        "classifications": classifications,
        "context_window_override": 4242,
        "lint_results": "lint digest",
        "progress": progress,
        "sensitivity": sensitivity,
        "force_semantic_chunking": True,
        "timeout": 12.5,
        "custom_agents": custom_agents,
        "run_builtin_checklist": False,
        "workspace_root": workspace_root,
        "context_collection_seconds": 1.5,
        "prior_state": prior_state,
        "force_full": True,
        "enforce_cost_cap": False,
        "stop": stop,
        "synthesis": synthesis,
    }
    assert_that(captured).is_length(1)
    received = captured[0]
    mismatched = sorted(
        name for name, value in expected.items() if getattr(received, name) != value
    )

    assert_that(received).is_same_as(options)
    assert_that(mismatched).is_empty()
    assert_that(sorted(expected)).is_equal_to(
        sorted(field.name for field in dataclasses.fields(ReviewSessionOptions)),
    )


def test_run_review_async_takes_the_same_single_surface() -> None:
    """The async entry point reads the options object the facade forwards."""
    parameters = inspect.signature(orchestrator.run_review_async).parameters

    assert_that(list(parameters)).is_equal_to(["context", "options"])
    assert_that(parameters["options"].annotation).is_equal_to(
        "ReviewSessionOptions",
    )

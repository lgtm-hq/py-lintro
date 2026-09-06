"""Characterization tests for the review session options object (#2301).

``run_review`` is the public facade and keeps its keyword signature; the values
it does not receive come from its own defaults, while every layer below reads
:class:`ReviewSessionOptions`. Until the final slice of #2301 collapses the two
onto one surface, the defaults live in two places, so they are pinned against
each other here: a default that drifts on one side silently changes what a
caller who omits the keyword gets.
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


def _run_review_keyword_defaults() -> dict[str, object]:
    """Collect the defaulted keyword parameters of ``run_review``.

    Returns:
        Mapping of parameter name to default value, for every keyword-only
        parameter of ``run_review`` that has a default.
    """
    return {
        name: parameter.default
        for name, parameter in inspect.signature(run_review).parameters.items()
        if parameter.kind is inspect.Parameter.KEYWORD_ONLY
        and parameter.default is not inspect.Parameter.empty
    }


def _session_option_defaults() -> dict[str, object]:
    """Collect the defaulted fields of ``ReviewSessionOptions``.

    Returns:
        Mapping of field name to default value, for every field of
        ``ReviewSessionOptions`` that has a plain (non-factory) default.
    """
    return {
        field.name: field.default
        for field in dataclasses.fields(ReviewSessionOptions)
        if field.default is not dataclasses.MISSING
    }


def test_every_defaulted_run_review_keyword_is_a_session_option() -> None:
    """No defaulted facade keyword is missing from the options object."""
    missing = sorted(
        set(_run_review_keyword_defaults()) - set(_session_option_defaults()),
    )

    assert_that(missing).is_empty()


def test_run_review_keyword_defaults_equal_session_option_defaults() -> None:
    """The facade and the options object agree on every shared default."""
    facade = _run_review_keyword_defaults()
    options = _session_option_defaults()

    shared = sorted(set(facade) & set(options))
    mismatched = [name for name in shared if facade[name] != options[name]]

    assert_that(shared).is_not_empty()
    assert_that(mismatched).is_empty()


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


def test_run_review_packs_every_facade_keyword_into_the_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each ``run_review`` keyword reaches ``run_review_async`` unchanged.

    The facade is the only place the keyword wall is packed, so a keyword
    dropped or wired to the wrong field there would silently ignore a caller's
    setting. Every keyword is given a value distinct from its default so a
    mis-wiring cannot pass by coincidence.

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

    orchestrator.run_review(
        ReviewContext(
            base_ref="base",
            head_ref="head",
            changed_files=[],
            unified_diff="",
        ),
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
    options = captured[0]
    mismatched = sorted(
        name for name, value in expected.items() if getattr(options, name) != value
    )

    assert_that(mismatched).is_empty()
    assert_that(sorted(expected)).is_equal_to(
        sorted(field.name for field in dataclasses.fields(ReviewSessionOptions)),
    )


def test_run_review_facade_keywords_cover_every_required_session_option() -> None:
    """Every option without a default is a keyword the facade accepts."""
    required = {
        field.name
        for field in dataclasses.fields(ReviewSessionOptions)
        if field.default is dataclasses.MISSING
        and field.default_factory is dataclasses.MISSING
    }
    facade = {
        name
        for name, parameter in inspect.signature(run_review).parameters.items()
        if parameter.kind is inspect.Parameter.KEYWORD_ONLY
    }

    assert_that(sorted(required - facade)).is_empty()

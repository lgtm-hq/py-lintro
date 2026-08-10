"""Tests for review user prompt construction."""

from __future__ import annotations

import re
from dataclasses import replace
from unittest.mock import patch

from assertpy import assert_that

from lintro.ai.review.checklist_registry import get_all_checklist_items
from lintro.ai.review.checklist_selector import select_checklist_items
from lintro.ai.review.models.file_classification import FileClassification
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.pipeline import prepare_review_user_prompt
from lintro.ai.review.prompt_builder import build_review_user_prompt

_LEAKED_KEY = "sk-abcdefghijklmnopqrstuvwxyz0123456789"


def test_build_review_user_prompt_includes_interaction_paths(
    sample_review_context: ReviewContext,
) -> None:
    """Prompt builder injects generated interaction paths into the user prompt."""
    from lintro.ai.review.classifier import classify_changed_files

    classifications = classify_changed_files(
        files=sample_review_context.changed_files,
    )
    checklist_items = select_checklist_items(
        classifications=classifications,
        items=get_all_checklist_items(),
    )

    with patch(
        "lintro.ai.review.prompt_builder.generate_interaction_paths",
        return_value="**Path A — CI + shell:** trace wiring",
    ) as generate_mock:
        prompt, prompt_mapping = build_review_user_prompt(
            context=sample_review_context,
            classifications=classifications,
            checklist_items=checklist_items,
        )

    generate_mock.assert_called_once()
    assert_that(prompt).contains("**Path A — CI + shell:** trace wiring")
    assert_that(prompt).contains("Interaction paths")
    assert_that(prompt_mapping).is_not_empty()


def test_build_review_user_prompt_fences_diff_with_unique_boundary(
    sample_review_context: ReviewContext,
) -> None:
    """Pipeline prompt builder nests the diff inside a per-call boundary fence."""
    classifications: list[FileClassification] = []
    checklist_items = select_checklist_items(
        classifications=classifications,
        items=get_all_checklist_items()[:1],
    )

    first, _ = build_review_user_prompt(
        context=sample_review_context,
        classifications=classifications,
        checklist_items=checklist_items,
    )
    second, _ = build_review_user_prompt(
        context=sample_review_context,
        classifications=classifications,
        checklist_items=checklist_items,
    )

    first_markers = set(re.findall(r"CODE_BLOCK_[0-9a-f]{8}", first))
    second_markers = set(re.findall(r"CODE_BLOCK_[0-9a-f]{8}", second))
    assert_that(first_markers).is_length(1)
    assert_that(second_markers).is_length(1)
    assert_that(first_markers).is_not_equal_to(second_markers)
    marker = next(iter(first_markers))
    assert_that(first).contains(f"<pull_request_diff>\n<{marker}>")
    assert_that(first).contains(f"</{marker}>\n</pull_request_diff>")


def test_prepare_review_user_prompt_redacts_secrets_in_diff(
    sample_review_context: ReviewContext,
) -> None:
    """Pipeline path redacts secrets before embedding the diff (#1884)."""
    leaked_diff = sample_review_context.unified_diff + f"\n+api_key = '{_LEAKED_KEY}'\n"
    context = replace(sample_review_context, unified_diff=leaked_diff)
    checklist_items = select_checklist_items(
        classifications=[],
        items=get_all_checklist_items()[:1],
    )

    prompt, _classifications, prompt_mapping = prepare_review_user_prompt(
        context=context,
        checklist_items=checklist_items,
    )

    assert_that(prompt).does_not_contain(_LEAKED_KEY)
    assert_that(prompt).contains("[REDACTED]")
    assert_that(prompt_mapping).is_not_empty()


def test_prepare_review_user_prompt_wires_paths_registry(
    sample_review_context: ReviewContext,
) -> None:
    """Pipeline prompt preparation calls the interaction path registry."""
    checklist_items = select_checklist_items(
        classifications=[],
        items=get_all_checklist_items()[:1],
    )

    with patch(
        "lintro.ai.review.pipeline.prompt_builder.build_review_user_prompt",
        wraps=build_review_user_prompt,
    ) as build_mock:
        prompt, classifications, prompt_mapping = prepare_review_user_prompt(
            context=sample_review_context,
            checklist_items=checklist_items,
        )

    build_mock.assert_called_once()
    assert_that(classifications).is_not_empty()
    assert_that(prompt).contains("Interaction paths")
    assert_that(prompt.lower()).contains("workflow")
    assert_that(prompt_mapping).is_not_empty()


def test_deferred_scope_is_fenced_and_redacted(
    sample_review_context: ReviewContext,
) -> None:
    """Deferred-scope text (PR-summary-derived) is fenced and redacted (#1884)."""
    checklist_items = select_checklist_items(
        classifications=[],
        items=get_all_checklist_items()[:1],
    )

    prompt, _ = build_review_user_prompt(
        context=sample_review_context,
        classifications=[],
        checklist_items=checklist_items,
        deferred_scope=f"perf work deferred; token {_LEAKED_KEY}",
    )

    marker = next(iter(re.findall(r"CODE_BLOCK_[0-9a-f]{8}", prompt)))
    deferred_index = prompt.find("perf work deferred")
    assert_that(deferred_index).is_greater_than_or_equal_to(0)
    fence_open = prompt.rfind(f"<{marker}>", 0, deferred_index)
    fence_close = prompt.find(f"</{marker}>", deferred_index)
    assert_that(fence_open).is_greater_than_or_equal_to(0)
    assert_that(fence_close).is_greater_than(deferred_index)
    assert_that(prompt).does_not_contain(_LEAKED_KEY)

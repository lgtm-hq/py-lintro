"""Tests for review user prompt construction."""

from __future__ import annotations

import importlib
from unittest.mock import patch

from assertpy import assert_that

from lintro.ai.review.checklist_registry import get_all_checklist_items
from lintro.ai.review.checklist_selector import select_checklist_items
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.prompt_builder import build_review_user_prompt


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


def test_prepare_review_user_prompt_wires_paths_registry(
    sample_review_context: ReviewContext,
) -> None:
    """Pipeline prompt preparation calls the interaction path registry."""
    import lintro.ai.review.pipeline as pipeline_module

    importlib.reload(pipeline_module)

    checklist_items = select_checklist_items(
        classifications=[],
        items=get_all_checklist_items()[:1],
    )

    with patch.object(
        pipeline_module.prompt_builder,
        "build_review_user_prompt",
        wraps=build_review_user_prompt,
    ) as build_mock:
        prompt, classifications, prompt_mapping = (
            pipeline_module.prepare_review_user_prompt(
                context=sample_review_context,
                checklist_items=checklist_items,
            )
        )

    build_mock.assert_called_once()
    assert_that(classifications).is_not_empty()
    assert_that(prompt).contains("Interaction paths")
    assert_that(prompt.lower()).contains("workflow")
    assert_that(prompt_mapping).is_not_empty()

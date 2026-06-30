"""Tests for checklist registry loading and validation."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.ai.review.checklist_builtin import BUILTIN_CHECKLIST_ITEMS
from lintro.ai.review.checklist_registry import (
    get_all_checklist_items,
    validate_checklist_items,
)
from lintro.ai.review.constants import CUSTOM_CHECKLIST_ID_START
from lintro.ai.review.enums.file_domain import FileDomain
from lintro.ai.review.enums.review_category import ReviewCategory
from lintro.ai.review.models.checklist_item import ChecklistItem
from lintro.config.lintro_config import LintroConfig
from lintro.config.review_config import (
    ReviewChecklistConfig,
    ReviewChecklistItemConfig,
    ReviewConfig,
)


def test_get_all_checklist_items_without_config_returns_builtin_only() -> None:
    """Registry without config returns builtin checklist items."""
    items = get_all_checklist_items()

    assert_that(items).is_equal_to(list(BUILTIN_CHECKLIST_ITEMS))


def test_get_all_checklist_items_loads_custom_config_items() -> None:
    """Custom review checklist items are appended with 10000+ ids."""
    config = LintroConfig(
        review=ReviewConfig(
            checklist=ReviewChecklistConfig(
                items=[
                    ReviewChecklistItemConfig(
                        question="Does any API handler skip auth?",
                        domains=[FileDomain.API],
                        languages=["python"],
                        category=ReviewCategory.SECURITY,
                    ),
                ],
            ),
        ),
    )

    items = get_all_checklist_items(config=config)
    custom_items = [item for item in items if item.id >= CUSTOM_CHECKLIST_ID_START]

    assert_that(custom_items).is_length(1)
    assert_that(custom_items[0].id).is_equal_to(CUSTOM_CHECKLIST_ID_START)
    assert_that(custom_items[0].tier).is_equal_to(2)
    assert_that(custom_items[0].domains).is_equal_to((FileDomain.API,))
    assert_that(custom_items[0].languages).is_equal_to(("python",))


def test_validate_checklist_items_rejects_duplicate_ids() -> None:
    """Duplicate checklist ids raise a validation error."""
    duplicate_items = [
        ChecklistItem(
            id=1,
            question="First",
            domains=(),
            languages=(),
            category=ReviewCategory.LOGIC_BUG,
            tier=1,
        ),
        ChecklistItem(
            id=1,
            question="Second",
            domains=(),
            languages=(),
            category=ReviewCategory.LOGIC_BUG,
            tier=1,
        ),
    ]

    with pytest.raises(ValueError, match="Duplicate checklist id"):
        validate_checklist_items(items=duplicate_items)


def test_validate_checklist_items_rejects_empty_questions() -> None:
    """Empty checklist questions raise a validation error."""
    items = [
        ChecklistItem(
            id=1,
            question="   ",
            domains=(),
            languages=(),
            category=ReviewCategory.LOGIC_BUG,
            tier=1,
        ),
    ]

    with pytest.raises(ValueError, match="empty question"):
        validate_checklist_items(items=items)


def test_validate_checklist_items_rejects_tier1_with_targets() -> None:
    """Tier 1 items must not declare domains or languages."""
    items = [
        ChecklistItem(
            id=1,
            question="Tier 1 item",
            domains=(FileDomain.SOURCE,),
            languages=(),
            category=ReviewCategory.LOGIC_BUG,
            tier=1,
        ),
    ]

    with pytest.raises(ValueError, match="must have empty domains"):
        validate_checklist_items(items=items)

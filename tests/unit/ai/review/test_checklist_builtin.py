"""Tests for built-in review checklist items."""

from __future__ import annotations

from assertpy import assert_that

from lintro.ai.review.checklist_builtin import (
    BUILTIN_CHECKLIST_ITEMS,
    TIER1_CHECKLIST_ITEMS,
    TIER2_CHECKLIST_ITEMS,
)
from lintro.ai.review.enums.review_category import ReviewCategory


def test_tier1_contains_fifteen_always_on_items() -> None:
    """Tier 1 includes exactly 15 universal checklist items."""
    assert_that(TIER1_CHECKLIST_ITEMS).is_length(15)
    assert_that(all(item.tier == 1 for item in TIER1_CHECKLIST_ITEMS)).is_true()
    assert_that(all(not item.triggers for item in TIER1_CHECKLIST_ITEMS)).is_true()


def test_tier1_ids_are_one_through_fifteen() -> None:
    """Tier 1 ids follow the locked 1-15 scheme."""
    assert_that([item.id for item in TIER1_CHECKLIST_ITEMS]).is_equal_to(
        list(range(1, 16)),
    )


def test_tier2_items_have_hundred_plus_ids_and_triggers() -> None:
    """Tier 2 items use 100+ ids and non-empty trigger globs."""
    assert_that(len(TIER2_CHECKLIST_ITEMS)).is_greater_than_or_equal_to(40)
    assert_that(all(item.id >= 100 for item in TIER2_CHECKLIST_ITEMS)).is_true()
    assert_that(all(item.tier == 2 for item in TIER2_CHECKLIST_ITEMS)).is_true()
    assert_that(all(item.triggers for item in TIER2_CHECKLIST_ITEMS)).is_true()


def test_builtin_items_have_unique_ids_and_nonempty_questions() -> None:
    """Built-in registry has unique ids and non-empty questions."""
    ids = [item.id for item in BUILTIN_CHECKLIST_ITEMS]
    assert_that(ids).does_not_contain_duplicates()
    assert_that(
        all(item.question.strip() for item in BUILTIN_CHECKLIST_ITEMS),
    ).is_true()


def test_tier1_security_item_covers_fail_open_enums() -> None:
    """Tier 1 includes the universal fail-open security question."""
    security_item = next(item for item in TIER1_CHECKLIST_ITEMS if item.id == 9)
    assert_that(security_item.category).is_equal_to(ReviewCategory.SECURITY)
    assert_that(security_item.question.lower()).contains("fail-open")

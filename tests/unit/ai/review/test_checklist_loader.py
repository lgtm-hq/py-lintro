"""Tests for the externalized checklist corpus loader."""

from __future__ import annotations

# nosemgrep: python.lang.compatibility.python37.python37-compatibility-importlib2
from importlib import resources
from typing import Any

import pytest
from assertpy import assert_that

from lintro.ai.review.checklist.loader import (
    _CORPUS_FILES,
    _parse_row,
    _validate_corpus,
    load_builtin_checklist,
)
from lintro.ai.review.enums.file_domain import FileDomain
from lintro.ai.review.enums.review_category import ReviewCategory
from lintro.ai.review.models.checklist_item import ChecklistItem


@pytest.fixture
def valid_row() -> dict[str, Any]:
    """Return a minimal valid Tier 2 corpus row.

    Returns:
        dict[str, Any]: A well-formed corpus row mapping.
    """
    return {
        "id": 100,
        "tier": 2,
        "category": "logic-bug",
        "domains": ["source"],
        "languages": ["python"],
        "question": "Does the change leak a resource on an error path?",
    }


def test_corpus_files_are_packaged_resources() -> None:
    """Every corpus YAML is discoverable as a packaged resource.

    This guards the wheel-packaging config: if ``corpus/*.yaml`` is not shipped
    as package data, the loader would fail at import in an installed wheel.
    """
    corpus = resources.files("lintro.ai.review.checklist").joinpath("corpus")
    for file_name in _CORPUS_FILES:
        resource = corpus.joinpath(file_name)
        assert_that(resource.is_file()).is_true()
        assert_that(resource.read_text(encoding="utf-8").strip()).is_not_empty()


def test_load_builtin_checklist_returns_populated_tuple() -> None:
    """The corpus loads into a non-empty tuple of ChecklistItem instances."""
    items = load_builtin_checklist()
    assert_that(items).is_type_of(tuple)
    assert_that(len(items)).is_greater_than(0)
    assert_that(all(isinstance(item, ChecklistItem) for item in items)).is_true()


def test_load_builtin_checklist_preserves_tier_split() -> None:
    """Loaded corpus keeps Tier 1 (1-15) and Tier 2 (100+) invariants."""
    items = load_builtin_checklist()
    tier1 = [item for item in items if item.tier == 1]
    tier2 = [item for item in items if item.tier == 2]
    assert_that(tier1).is_length(15)
    assert_that([item.id for item in tier1]).is_equal_to(list(range(1, 16)))
    assert_that(all(item.id >= 100 for item in tier2)).is_true()
    assert_that(len(tier2)).is_greater_than_or_equal_to(40)


def test_load_builtin_checklist_is_deterministic() -> None:
    """Reloading the corpus yields identical items in identical order."""
    assert_that(load_builtin_checklist()).is_equal_to(load_builtin_checklist())


def test_parse_row_builds_expected_item(valid_row: dict[str, Any]) -> None:
    """A valid row parses into the expected ChecklistItem."""
    item = _parse_row(row=valid_row)
    assert_that(item.id).is_equal_to(100)
    assert_that(item.category).is_equal_to(ReviewCategory.LOGIC_BUG)
    assert_that(item.domains).is_equal_to((FileDomain.SOURCE,))
    assert_that(item.languages).is_equal_to(("python",))


def test_parse_row_rejects_missing_field(valid_row: dict[str, Any]) -> None:
    """Removing a required field fails fast."""
    del valid_row["question"]
    assert_that(_parse_row).raises(ValueError).when_called_with(
        row=valid_row,
    ).contains("missing fields")


def test_parse_row_rejects_unexpected_field(valid_row: dict[str, Any]) -> None:
    """An unknown field fails fast rather than being silently ignored."""
    valid_row["triggers"] = ["source"]
    assert_that(_parse_row).raises(ValueError).when_called_with(
        row=valid_row,
    ).contains("unexpected fields")


def test_parse_row_rejects_invalid_category(valid_row: dict[str, Any]) -> None:
    """An unknown category value fails fast."""
    valid_row["category"] = "not-a-category"
    assert_that(_parse_row).raises(ValueError).when_called_with(
        row=valid_row,
    ).contains("invalid category")


def test_parse_row_rejects_invalid_domain(valid_row: dict[str, Any]) -> None:
    """An unknown FileDomain tag fails fast."""
    valid_row["domains"] = ["nonsense-domain"]
    assert_that(_parse_row).raises(ValueError).when_called_with(
        row=valid_row,
    ).contains("invalid domain")


def test_parse_row_rejects_unknown_language(valid_row: dict[str, Any]) -> None:
    """An unknown identify language tag fails fast."""
    valid_row["languages"] = ["klingon"]
    assert_that(_parse_row).raises(ValueError).when_called_with(
        row=valid_row,
    ).contains("unknown language tags")


def test_validate_corpus_rejects_duplicate_id() -> None:
    """Two items sharing an id are rejected."""
    a = ChecklistItem(
        id=100,
        question="First question about resource cleanup?",
        domains=(),
        languages=(),
        category=ReviewCategory.LOGIC_BUG,
        tier=2,
    )
    b = ChecklistItem(
        id=100,
        question="Second distinct question about error handling?",
        domains=(),
        languages=(),
        category=ReviewCategory.LOGIC_BUG,
        tier=2,
    )
    assert_that(_validate_corpus).raises(ValueError).when_called_with(
        items=(a, b),
    ).contains("Duplicate checklist id")


def test_validate_corpus_rejects_duplicate_question() -> None:
    """Two items with the same normalized question are rejected."""
    a = ChecklistItem(
        id=100,
        question="Does   the change leak a resource?",
        domains=(),
        languages=(),
        category=ReviewCategory.LOGIC_BUG,
        tier=2,
    )
    b = ChecklistItem(
        id=101,
        question="Does the change leak a resource?",
        domains=(),
        languages=(),
        category=ReviewCategory.LOGIC_BUG,
        tier=2,
    )
    assert_that(_validate_corpus).raises(ValueError).when_called_with(
        items=(a, b),
    ).contains("Duplicate checklist question")


def test_validate_corpus_rejects_tier1_id_out_of_range() -> None:
    """A Tier 1 item outside the 1-15 range is rejected."""
    item = ChecklistItem(
        id=99,
        question="A Tier 1 question with an out-of-range id?",
        domains=(),
        languages=(),
        category=ReviewCategory.LOGIC_BUG,
        tier=1,
    )
    assert_that(_validate_corpus).raises(ValueError).when_called_with(
        items=(item,),
    ).contains("must use id")


def test_validate_corpus_rejects_tier1_with_axes() -> None:
    """A Tier 1 item with non-empty domains/languages is rejected."""
    item = ChecklistItem(
        id=1,
        question="A Tier 1 question that wrongly declares a domain?",
        domains=(FileDomain.SOURCE,),
        languages=(),
        category=ReviewCategory.LOGIC_BUG,
        tier=1,
    )
    assert_that(_validate_corpus).raises(ValueError).when_called_with(
        items=(item,),
    ).contains("must have empty domains")


def test_validate_corpus_rejects_tier2_id_below_start() -> None:
    """A Tier 2 item with an id below 100 is rejected."""
    item = ChecklistItem(
        id=42,
        question="A Tier 2 question with too small an id?",
        domains=(FileDomain.SOURCE,),
        languages=(),
        category=ReviewCategory.LOGIC_BUG,
        tier=2,
    )
    assert_that(_validate_corpus).raises(ValueError).when_called_with(
        items=(item,),
    ).contains("must use id")

"""Tests for checklist selection and prompt formatting."""

from __future__ import annotations

from assertpy import assert_that

from lintro.ai.review.checklist_builtin import BUILTIN_CHECKLIST_ITEMS
from lintro.ai.review.checklist_selector import (
    format_checklist_for_prompt,
    select_checklist_items,
)
from lintro.ai.review.constants import CUSTOM_CHECKLIST_ID_START
from lintro.ai.review.enums.review_category import ReviewCategory
from lintro.ai.review.models.checklist_item import ChecklistItem
from lintro.config.lintro_config import LintroConfig
from lintro.config.review_config import (
    ReviewChecklistConfig,
    ReviewChecklistItemConfig,
    ReviewConfig,
)


def test_select_checklist_items_always_includes_tier1() -> None:
    """Tier 1 items are selected regardless of changed files."""
    selected = select_checklist_items(
        changed_files=["README.md"],
        items=list(BUILTIN_CHECKLIST_ITEMS),
    )

    tier1_ids = {item.id for item in selected if item.tier == 1}
    assert_that(tier1_ids).is_equal_to(set(range(1, 16)))


def test_empty_changed_files_still_returns_tier1() -> None:
    """Empty changed file lists still include all Tier 1 items."""
    selected = select_checklist_items(
        changed_files=[],
        items=list(BUILTIN_CHECKLIST_ITEMS),
    )

    assert_that([item.id for item in selected if item.tier == 1]).is_equal_to(
        list(range(1, 16)),
    )
    assert_that(all(item.tier == 1 for item in selected)).is_true()


def test_select_checklist_items_includes_tier2_when_globs_match() -> None:
    """Tier 2 items are included only when trigger globs match changed files."""
    selected = select_checklist_items(
        changed_files=["src/main.py"],
        items=list(BUILTIN_CHECKLIST_ITEMS),
    )

    assert_that(any(item.id == 101 for item in selected)).is_true()
    assert_that(any(item.id == 103 for item in selected)).is_false()


def test_select_checklist_items_includes_rust_tier2_for_rs_files() -> None:
    """Rust-specific Tier 2 items match .rs changed files."""
    selected = select_checklist_items(
        changed_files=["src/lib.rs"],
        items=list(BUILTIN_CHECKLIST_ITEMS),
    )

    assert_that(any(item.id == 103 for item in selected)).is_true()


def test_select_checklist_items_includes_ci_tier2_for_dotgithub_workflows() -> None:
    """Dot-prefixed .github paths must match CI Tier 2 checklist triggers."""
    selected = select_checklist_items(
        changed_files=[".github/workflows/ci.yml"],
        items=list(BUILTIN_CHECKLIST_ITEMS),
    )

    assert_that(any(item.id == 124 for item in selected)).is_true()
    assert_that(any(item.id == 150 for item in selected)).is_true()


def test_select_checklist_items_includes_custom_config_when_trigger_matches() -> None:
    """Custom config items follow Tier 2 trigger selection rules."""
    custom_item = ChecklistItem(
        id=CUSTOM_CHECKLIST_ID_START,
        question="Does any Django view miss @login_required?",
        triggers=["**/views.py"],
        category=ReviewCategory.SECURITY,
        tier=2,
    )
    items = list(BUILTIN_CHECKLIST_ITEMS) + [custom_item]

    matched = select_checklist_items(
        changed_files=["app/views.py"],
        items=items,
    )
    unmatched = select_checklist_items(
        changed_files=["app/models.py"],
        items=items,
    )

    assert_that(any(item.id == CUSTOM_CHECKLIST_ID_START for item in matched)).is_true()
    assert_that(
        any(item.id == CUSTOM_CHECKLIST_ID_START for item in unmatched),
    ).is_false()


def test_select_checklist_items_returns_sorted_by_id() -> None:
    """Selected checklist items are sorted by stable id."""
    selected = select_checklist_items(
        changed_files=["src/main.py", ".github/workflows/ci.yml"],
        items=list(BUILTIN_CHECKLIST_ITEMS),
    )

    assert_that([item.id for item in selected]).is_equal_to(
        sorted(item.id for item in selected),
    )


def test_format_checklist_for_prompt_renumbers_sequentially() -> None:
    """Prompt formatting renumbers checklist items from one."""
    selected = select_checklist_items(
        changed_files=["src/main.py"],
        items=list(BUILTIN_CHECKLIST_ITEMS),
    )
    prompt_text, prompt_mapping = format_checklist_for_prompt(items=selected)

    assert_that(prompt_text.splitlines()[0]).starts_with("1.")
    assert_that(prompt_mapping[1]).is_equal_to(selected[0].id)
    assert_that(len(prompt_mapping)).is_equal_to(len(selected))


def test_select_checklist_items_matches_root_level_files_for_double_star_globs() -> (
    None
):
    """Root-level files match ``**/`` trigger patterns."""
    custom_item = ChecklistItem(
        id=CUSTOM_CHECKLIST_ID_START,
        question="Does any Django view miss @login_required?",
        triggers=["**/views.py"],
        category=ReviewCategory.SECURITY,
        tier=2,
    )
    items = list(BUILTIN_CHECKLIST_ITEMS) + [custom_item]

    selected = select_checklist_items(
        changed_files=["views.py"],
        items=items,
    )

    assert_that(
        any(item.id == CUSTOM_CHECKLIST_ID_START for item in selected),
    ).is_true()


def test_custom_config_end_to_end_selection() -> None:
    """Config-loaded custom items participate in end-to-end selection."""
    from lintro.ai.review.checklist_registry import get_all_checklist_items

    config = LintroConfig(
        review=ReviewConfig(
            checklist=ReviewChecklistConfig(
                items=[
                    ReviewChecklistItemConfig(
                        question="Does any Django view miss @login_required?",
                        triggers=["**/views.py"],
                        category=ReviewCategory.SECURITY,
                    ),
                ],
            ),
        ),
    )
    items = get_all_checklist_items(config=config)
    selected = select_checklist_items(
        changed_files=["project/views.py"],
        items=items,
    )

    assert_that(
        any(item.id == CUSTOM_CHECKLIST_ID_START for item in selected),
    ).is_true()

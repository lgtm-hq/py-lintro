"""Tests for checklist selection and prompt formatting."""

from __future__ import annotations

from assertpy import assert_that

from lintro.ai.review.checklist_builtin import BUILTIN_CHECKLIST_ITEMS
from lintro.ai.review.checklist_selector import (
    format_checklist_for_prompt,
    select_checklist_items,
)
from lintro.ai.review.classifier import classify_changed_files
from lintro.ai.review.constants import CUSTOM_CHECKLIST_ID_START
from lintro.ai.review.enums.changed_file_status import ChangedFileStatus
from lintro.ai.review.enums.file_domain import FileDomain
from lintro.ai.review.enums.review_category import ReviewCategory
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.checklist_item import ChecklistItem
from lintro.ai.review.models.file_classification import FileClassification
from lintro.config.lintro_config import LintroConfig
from lintro.config.review_config import (
    ReviewChecklistConfig,
    ReviewChecklistItemConfig,
    ReviewConfig,
)


def _classify(paths: list[str]) -> list[FileClassification]:
    """Classify repository paths into review domains for selection tests."""
    changed_files = [
        ChangedFile(
            path=path,
            status=ChangedFileStatus.MODIFIED,
            additions=1,
            deletions=0,
        )
        for path in paths
    ]
    return classify_changed_files(changed_files)


def test_select_checklist_items_always_includes_tier1() -> None:
    """Tier 1 items are selected regardless of changed files."""
    selected = select_checklist_items(
        classifications=_classify(["README.md"]),
        items=list(BUILTIN_CHECKLIST_ITEMS),
    )

    tier1_ids = {item.id for item in selected if item.tier == 1}
    assert_that(tier1_ids).is_equal_to(set(range(1, 16)))


def test_empty_changed_files_still_returns_tier1() -> None:
    """Empty changed file lists still include all Tier 1 items only."""
    selected = select_checklist_items(
        classifications=[],
        items=list(BUILTIN_CHECKLIST_ITEMS),
    )

    assert_that([item.id for item in selected if item.tier == 1]).is_equal_to(
        list(range(1, 16)),
    )
    assert_that(all(item.tier == 1 for item in selected)).is_true()


def test_select_checklist_items_includes_source_tier2_for_python() -> None:
    """Source-domain Tier 2 items are included for Python source, not Rust ones."""
    selected = select_checklist_items(
        classifications=_classify(["src/main.py"]),
        items=list(BUILTIN_CHECKLIST_ITEMS),
    )

    selected_ids = {item.id for item in selected}
    assert_that(selected_ids).contains(101)
    assert_that(selected_ids).does_not_contain(103)


def test_select_checklist_items_includes_rust_tier2_for_rs_files() -> None:
    """Rust-language Tier 2 items match .rs changed files."""
    selected = select_checklist_items(
        classifications=_classify(["src/lib.rs"]),
        items=list(BUILTIN_CHECKLIST_ITEMS),
    )

    assert_that({item.id for item in selected}).contains(103)


def test_select_checklist_items_includes_ci_tier2_for_dotgithub_workflows() -> None:
    """CI-domain Tier 2 items match .github workflow paths."""
    selected = select_checklist_items(
        classifications=_classify([".github/workflows/ci.yml"]),
        items=list(BUILTIN_CHECKLIST_ITEMS),
    )

    selected_ids = {item.id for item in selected}
    assert_that(selected_ids).contains(124)
    assert_that(selected_ids).contains(150)


def test_universal_tier2_items_included_for_any_nonempty_diff() -> None:
    """Tier 2 items with no domains or languages fire on any non-empty diff."""
    selected = select_checklist_items(
        classifications=_classify(["README.md"]),
        items=list(BUILTIN_CHECKLIST_ITEMS),
    )

    assert_that({item.id for item in selected}).contains(100)


def test_select_checklist_items_matches_custom_domain_item() -> None:
    """Custom items follow Tier 2 domain selection rules."""
    custom_item = ChecklistItem(
        id=CUSTOM_CHECKLIST_ID_START,
        question="Does any API handler skip auth?",
        domains=(FileDomain.API,),
        languages=(),
        category=ReviewCategory.SECURITY,
        tier=2,
    )
    items = [*BUILTIN_CHECKLIST_ITEMS, custom_item]

    matched = select_checklist_items(
        classifications=_classify(["app/api/users.py"]),
        items=items,
    )
    unmatched = select_checklist_items(
        classifications=_classify(["docs/intro.md"]),
        items=items,
    )

    assert_that(any(item.id == CUSTOM_CHECKLIST_ID_START for item in matched)).is_true()
    assert_that(
        any(item.id == CUSTOM_CHECKLIST_ID_START for item in unmatched),
    ).is_false()


def test_select_checklist_items_matches_custom_language_item() -> None:
    """Custom items can target a specific language via identify tags."""
    custom_item = ChecklistItem(
        id=CUSTOM_CHECKLIST_ID_START,
        question="Does Go code ignore returned errors?",
        domains=(),
        languages=("go",),
        category=ReviewCategory.LOGIC_BUG,
        tier=2,
    )
    items = [*BUILTIN_CHECKLIST_ITEMS, custom_item]

    matched = select_checklist_items(
        classifications=_classify(["cmd/server/main.go"]),
        items=items,
    )
    unmatched = select_checklist_items(
        classifications=_classify(["src/main.py"]),
        items=items,
    )

    assert_that(any(item.id == CUSTOM_CHECKLIST_ID_START for item in matched)).is_true()
    assert_that(
        any(item.id == CUSTOM_CHECKLIST_ID_START for item in unmatched),
    ).is_false()


def test_select_checklist_items_returns_sorted_by_id() -> None:
    """Selected checklist items are sorted by stable id."""
    selected = select_checklist_items(
        classifications=_classify(["src/main.py", ".github/workflows/ci.yml"]),
        items=list(BUILTIN_CHECKLIST_ITEMS),
    )

    assert_that([item.id for item in selected]).is_equal_to(
        sorted(item.id for item in selected),
    )


def test_format_checklist_for_prompt_renumbers_sequentially() -> None:
    """Prompt formatting renumbers checklist items from one."""
    selected = select_checklist_items(
        classifications=_classify(["src/main.py"]),
        items=list(BUILTIN_CHECKLIST_ITEMS),
    )
    prompt_text, prompt_mapping = format_checklist_for_prompt(items=selected)

    assert_that(prompt_text.splitlines()[0]).starts_with("1.")
    assert_that(prompt_mapping[1]).is_equal_to(selected[0].id)
    assert_that(len(prompt_mapping)).is_equal_to(len(selected))


def test_format_checklist_for_prompt_collapses_internal_whitespace() -> None:
    """Prompt lines stay single-line even when a question contains extra spaces."""
    item = ChecklistItem(
        id=10_000,
        question="Does  any\nhandler skip   auth?",
        domains=(),
        languages=(),
        category=ReviewCategory.SECURITY,
        tier=1,
    )

    prompt_text, prompt_mapping = format_checklist_for_prompt(items=[item])

    assert_that(prompt_text).is_equal_to(
        "1. [security] Does any handler skip auth?",
    )
    assert_that(prompt_mapping).is_equal_to({1: 10_000})


def test_select_checklist_items_includes_go_boundary_validation_item() -> None:
    """Go API boundary files activate the input-validation security checklist item."""
    selected = select_checklist_items(
        classifications=_classify(["api/handlers.go"]),
        items=list(BUILTIN_CHECKLIST_ITEMS),
    )

    assert_that({item.id for item in selected}).contains(149)


def test_dual_axis_item_requires_both_domain_and_language() -> None:
    """Items with both axes only match when domain and language intersect."""
    custom_item = ChecklistItem(
        id=CUSTOM_CHECKLIST_ID_START,
        question="Does any API handler skip auth?",
        domains=(FileDomain.API,),
        languages=("python",),
        category=ReviewCategory.SECURITY,
        tier=2,
    )
    items = [*BUILTIN_CHECKLIST_ITEMS, custom_item]

    matched = select_checklist_items(
        classifications=_classify(["project/api/views.py"]),
        items=items,
    )
    language_only = select_checklist_items(
        classifications=_classify(["scripts/migrate.py"]),
        items=items,
    )

    assert_that(any(item.id == CUSTOM_CHECKLIST_ID_START for item in matched)).is_true()
    assert_that(
        any(item.id == CUSTOM_CHECKLIST_ID_START for item in language_only),
    ).is_false()


def test_e2e_checklist_items_do_not_match_unit_tests() -> None:
    """Browser-specific checklist items skip ordinary unit test files."""
    selected = select_checklist_items(
        classifications=_classify(["tests/unit/service.test.ts"]),
        items=list(BUILTIN_CHECKLIST_ITEMS),
    )

    assert_that({item.id for item in selected}).does_not_contain(110, 111, 128, 129)


def test_e2e_checklist_items_match_e2e_paths() -> None:
    """Browser-specific checklist items activate for E2E test paths."""
    selected = select_checklist_items(
        classifications=_classify(["tests/e2e/login.spec.ts"]),
        items=list(BUILTIN_CHECKLIST_ITEMS),
    )

    assert_that({item.id for item in selected}).contains(110)


def test_dual_axis_item_does_not_match_across_unrelated_files() -> None:
    """Dual-axis items require both axes on the same changed file."""
    custom_item = ChecklistItem(
        id=CUSTOM_CHECKLIST_ID_START,
        question="Does any API handler skip auth?",
        domains=(FileDomain.API,),
        languages=("python",),
        category=ReviewCategory.SECURITY,
        tier=2,
    )
    items = [*BUILTIN_CHECKLIST_ITEMS, custom_item]
    cross_file = [
        *_classify(["api/schema.yaml"]),
        *_classify(["scripts/migrate.py"]),
    ]

    selected = select_checklist_items(classifications=cross_file, items=items)

    assert_that(
        any(item.id == CUSTOM_CHECKLIST_ID_START for item in selected),
    ).is_false()


def test_dual_axis_shell_python_item_does_not_match_plain_python() -> None:
    """Shell+Python items do not fire on ordinary Python source files."""
    selected = select_checklist_items(
        classifications=_classify(["src/config.py"]),
        items=list(BUILTIN_CHECKLIST_ITEMS),
    )

    assert_that({item.id for item in selected}).does_not_contain(148)


def test_select_checklist_items_matches_root_level_source_files() -> None:
    """Root-level source files still resolve a language for selection."""
    selected = select_checklist_items(
        classifications=_classify(["setup.py"]),
        items=list(BUILTIN_CHECKLIST_ITEMS),
    )

    assert_that({item.id for item in selected}).contains(151)


def test_custom_config_end_to_end_selection() -> None:
    """Config-loaded custom items participate in end-to-end selection."""
    from lintro.ai.review.checklist_registry import get_all_checklist_items

    config = LintroConfig(
        review=ReviewConfig(
            checklist=ReviewChecklistConfig(
                items=[
                    ReviewChecklistItemConfig(
                        question="Does any API handler skip auth?",
                        domains=[FileDomain.API],
                        category=ReviewCategory.SECURITY,
                    ),
                ],
            ),
        ),
    )
    items = get_all_checklist_items(config=config)
    selected = select_checklist_items(
        classifications=_classify(["project/api/views.py"]),
        items=items,
    )

    assert_that(
        any(item.id == CUSTOM_CHECKLIST_ID_START for item in selected),
    ).is_true()

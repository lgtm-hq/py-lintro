"""Tests for review configuration parsing."""

from __future__ import annotations

from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.ai.review.constants import CUSTOM_CHECKLIST_ID_START
from lintro.ai.review.enums.review_category import ReviewCategory
from lintro.ai.review.enums.review_strictness import ReviewStrictness
from lintro.ai.review.enums.checklist_display import ChecklistDisplay
from lintro.config.config_loader import clear_config_cache, load_config
from lintro.config.review_config import (
    ReviewChecklistItemConfig,
)


def test_load_config_parses_review_checklist_items(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """YAML review.checklist.items are loaded into LintroConfig."""
    config_file = tmp_path / ".lintro-config.yaml"
    config_file.write_text(
        (
            "review:\n"
            "  checklist:\n"
            "    items:\n"
            "      - question: Does any Django view miss @login_required?\n"
            "        triggers:\n"
            "          - '**/views.py'\n"
            "        category: security\n"
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    clear_config_cache()

    config = load_config(config_path=config_file)

    assert_that(config.review.checklist.items).is_length(1)
    item = config.review.checklist.items[0]
    assert_that(item.question).contains("@login_required")
    assert_that(item.triggers).is_equal_to(["**/views.py"])
    assert_that(item.category).is_equal_to(ReviewCategory.SECURITY)


def test_load_config_defaults_review_section_when_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing review section defaults to empty checklist config."""
    config_file = tmp_path / ".lintro-config.yaml"
    config_file.write_text("execution:\n  parallel: false\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    clear_config_cache()

    config = load_config(config_path=config_file)

    assert_that(config.review.checklist.items).is_empty()
    assert_that(config.review.strictness).is_equal_to(ReviewStrictness.BALANCED)


def test_load_config_parses_review_strictness_and_overrides(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """YAML review.strictness and sensitivity overrides are loaded."""
    config_file = tmp_path / ".lintro-config.yaml"
    config_file.write_text(
        (
            "review:\n"
            "  strictness: focused\n"
            "  sensitivity:\n"
            "    migration_notes: true\n"
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    clear_config_cache()

    config = load_config(config_path=config_file)

    assert_that(config.review.strictness).is_equal_to(ReviewStrictness.FOCUSED)
    assert_that(config.review.sensitivity.migration_notes).is_true()


def test_load_config_parses_review_depth_and_semantic_chunking(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """YAML review.depth and force_semantic_chunking are loaded."""
    config_file = tmp_path / ".lintro-config.yaml"
    config_file.write_text(
        ("review:\n" "  depth: 2\n" "  force_semantic_chunking: true\n"),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    clear_config_cache()

    config = load_config(config_path=config_file)

    assert_that(config.review.depth).is_equal_to(2)
    assert_that(config.review.force_semantic_chunking).is_true()


def test_load_config_parses_checklist_display(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """YAML review.checklist_display is loaded."""
    config_file = tmp_path / ".lintro-config.yaml"
    config_file.write_text(
        "review:\n  checklist_display: linked\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    clear_config_cache()

    config = load_config(config_path=config_file)

    assert_that(config.review.checklist_display).is_equal_to(
        ChecklistDisplay.LINKED,
    )


def test_review_config_rejects_invalid_depth() -> None:
    """Review depth must be between 1 and 3."""
    from lintro.config.review_config import ReviewConfig

    with pytest.raises(ValueError):
        ReviewConfig(depth=0)


def test_custom_checklist_id_start_is_ten_thousand() -> None:
    """Custom checklist ids start at 10000 per phase spec."""
    assert_that(CUSTOM_CHECKLIST_ID_START).is_equal_to(10_000)


def test_review_checklist_item_rejects_empty_question() -> None:
    """Empty custom checklist questions fail config validation."""
    with pytest.raises(ValueError, match="must not be empty"):
        ReviewChecklistItemConfig(
            question="   ",
            triggers=["**/views.py"],
            category=ReviewCategory.SECURITY,
        )


def test_review_checklist_item_rejects_empty_triggers() -> None:
    """Custom checklist items require at least one trigger glob."""
    with pytest.raises(ValueError, match="at least one glob pattern"):
        ReviewChecklistItemConfig(
            question="Does any Django view miss @login_required?",
            triggers=[],
            category=ReviewCategory.SECURITY,
        )

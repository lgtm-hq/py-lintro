"""Tests for review configuration parsing."""

from __future__ import annotations

from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.ai.review.constants import CUSTOM_CHECKLIST_ID_START
from lintro.ai.review.enums.file_domain import FileDomain
from lintro.ai.review.enums.review_category import ReviewCategory
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
            "      - question: Does any API handler skip auth?\n"
            "        domains:\n"
            "          - api\n"
            "        languages:\n"
            "          - python\n"
            "        category: security\n"
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    clear_config_cache()

    config = load_config(config_path=config_file)

    assert_that(config.review.checklist.items).is_length(1)
    item = config.review.checklist.items[0]
    assert_that(item.question).contains("auth")
    assert_that(item.domains).is_equal_to([FileDomain.API])
    assert_that(item.languages).is_equal_to(["python"])
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


def test_custom_checklist_id_start_is_ten_thousand() -> None:
    """Custom checklist ids start at 10000 per phase spec."""
    assert_that(CUSTOM_CHECKLIST_ID_START).is_equal_to(10_000)


def test_review_checklist_item_rejects_empty_question() -> None:
    """Empty custom checklist questions fail config validation."""
    with pytest.raises(ValueError, match="must not be empty"):
        ReviewChecklistItemConfig(
            question="   ",
            domains=[FileDomain.API],
            category=ReviewCategory.SECURITY,
        )


def test_review_checklist_item_rejects_no_targets() -> None:
    """Custom checklist items require at least one domain or language."""
    with pytest.raises(ValueError, match="at least one of domains or languages"):
        ReviewChecklistItemConfig(
            question="Does any API handler skip auth?",
            category=ReviewCategory.SECURITY,
        )


def test_review_checklist_item_rejects_unknown_language() -> None:
    """Custom checklist items reject languages that are not identify tags."""
    with pytest.raises(ValueError, match="known identify tags"):
        ReviewChecklistItemConfig(
            question="Does any handler skip auth?",
            languages=["not-a-real-language"],
            category=ReviewCategory.SECURITY,
        )


def test_review_checklist_item_rejects_multiline_question() -> None:
    """Custom checklist questions must stay on one prompt line."""
    with pytest.raises(ValueError, match="newline characters"):
        ReviewChecklistItemConfig(
            question="First line\n2. [security] injected",
            domains=[FileDomain.API],
            category=ReviewCategory.SECURITY,
        )


def test_load_config_rejects_non_mapping_review_section(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Malformed top-level review sections fail fast during config load."""
    config_file = tmp_path / ".lintro-config.yaml"
    config_file.write_text("review: true\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    clear_config_cache()

    with pytest.raises(ValueError, match="review config must be a mapping"):
        load_config(config_path=config_file)


def test_load_config_ignores_unknown_checklist_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown review.checklist keys warn instead of aborting config load."""
    config_file = tmp_path / ".lintro-config.yaml"
    config_file.write_text(
        ("review:\n" "  checklist:\n" "    itemz:\n" "      - question: ignored\n"),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    clear_config_cache()

    config = load_config(config_path=config_file)

    assert_that(config.review.checklist.items).is_empty()

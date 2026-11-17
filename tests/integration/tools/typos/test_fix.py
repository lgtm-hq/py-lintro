"""Integration tests for TyposPlugin fix command."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from assertpy import assert_that

from tests.integration._tools import require_tool

if TYPE_CHECKING:
    from lintro.plugins.base import BaseToolPlugin

pytestmark = require_tool("typos")


def test_fix_corrects_real_typos(
    get_plugin: Callable[[str], BaseToolPlugin],
    typos_violation_file: str,
) -> None:
    """Verify typos auto-corrects misspellings and reports fixed counts.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        typos_violation_file: Path to the copied violation sample.
    """
    plugin = get_plugin("typos")

    result = plugin.fix([typos_violation_file], {})

    assert_that(result.name).is_equal_to("typos")
    assert_that(result.fixed_issues_count).is_greater_than_or_equal_to(2)
    # Invariant: initial == fixed + remaining.
    assert_that(
        (result.fixed_issues_count or 0) + (result.remaining_issues_count or 0),
    ).is_equal_to(result.initial_issues_count)
    # The fixture is fully correctable, so the fix must complete cleanly.
    assert_that(result.remaining_issues_count).is_equal_to(0)
    assert_that(result.success).is_true()

    fixed_content = Path(typos_violation_file).read_text(encoding="utf-8")
    assert_that(fixed_content).contains("sample")
    assert_that(fixed_content).contains("separate")
    assert_that(fixed_content).contains("the words")
    assert_that(fixed_content).contains("spell checker")
    assert_that(fixed_content).does_not_contain("teh")


def test_fix_clean_file_is_noop(
    get_plugin: Callable[[str], BaseToolPlugin],
    typos_clean_file: str,
) -> None:
    """Verify fixing an already-clean file changes nothing and succeeds.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        typos_clean_file: Path to the copied clean sample.
    """
    plugin = get_plugin("typos")
    before = Path(typos_clean_file).read_text(encoding="utf-8")

    result = plugin.fix([typos_clean_file], {})

    assert_that(result.success).is_true()
    assert_that(result.initial_issues_count).is_equal_to(0)
    assert_that(result.fixed_issues_count).is_equal_to(0)
    assert_that(result.remaining_issues_count).is_equal_to(0)
    assert_that(Path(typos_clean_file).read_text(encoding="utf-8")).is_equal_to(before)


def test_fix_leaves_http_verb_plurals_alone_under_repo_config(
    get_plugin: Callable[[str], BaseToolPlugin],
    typos_project_config_dir: Path,
) -> None:
    """Verify the repo's ``.typos.toml`` keeps ``DELETEs`` from becoming ``DELETEEs``.

    typos splits ``DELETEs`` into ``DELET`` + ``Es`` and its fix inserts an
    ``E`` (#3509). The project config is copied next to the sample so typos
    discovers it the way it does inside the repository.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        typos_project_config_dir: Temp directory holding the repo's config.
    """
    sample = typos_project_config_dir / "verbs.txt"
    text = (
        "three failed rechecks or DELETEs in a row\n"
        "GETs, PUTs, POSTs, PATCHes, HEADs, CONNECTs and TRACEs\n"
        "UPDATEs, SELECTs, INSERTs, ROTATEs, DELETEd and PATCHed\n"
        "x_DELETEs, my_UPDATEd_rows and test_handles_DELETEs\n"
        "DELETEs_DELETEs, GETs_PUTs_DELETEs and x_UPDATEd_DELETEs_y\n"
    )
    sample.write_text(text, encoding="utf-8")
    plugin = get_plugin("typos")

    result = plugin.fix([str(sample)], {})

    assert_that(result.success).is_true()
    assert_that(result.initial_issues_count).is_equal_to(0)
    assert_that(sample.read_text(encoding="utf-8")).is_equal_to(text)


def test_repo_config_still_reports_misspellings_near_verb_plurals(
    get_plugin: Callable[[str], BaseToolPlugin],
    typos_project_config_dir: Path,
) -> None:
    """Verify the verb-plural ignore pattern only covers whole inflected words.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        typos_project_config_dir: Temp directory holding the repo's config.
    """
    sample = typos_project_config_dir / "near.txt"
    sample.write_text("DELET, DELETEsx and delets\n", encoding="utf-8")
    plugin = get_plugin("typos")

    result = plugin.check([str(sample)], {})

    assert_that([getattr(i, "typo", None) for i in result.issues or []]).is_equal_to(
        ["DELET", "DELET", "delets"],
    )


def test_fix_never_renames_a_misspelled_file(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """Verify fix corrects the content but reports, not renames, the file name.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest temporary directory fixture.
    """
    sample = tmp_path / "recieve.txt"
    sample.write_text("teh cat\n", encoding="utf-8")
    plugin = get_plugin("typos")

    result = plugin.fix([str(sample)], {})

    assert_that((tmp_path / "receive.txt").exists()).is_false()
    assert_that(sample.read_text(encoding="utf-8")).is_equal_to("the cat\n")
    assert_that(result.fixed_issues_count).is_equal_to(1)
    assert_that([getattr(i, "typo", None) for i in result.issues or []]).is_equal_to(
        ["recieve"],
    )

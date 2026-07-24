"""Unit tests for tsconfig checkJs resolution (issue #1185)."""

from __future__ import annotations

from pathlib import Path

from assertpy import assert_that

from lintro.utils.tsconfig import enables_check_js
from tests.unit.utils.tsconfig_helpers import write_tsconfig


def test_enables_check_js_true(tmp_path: Path) -> None:
    """Direct checkJs: true is detected.

    Args:
        tmp_path: Pytest temporary directory.
    """
    path = write_tsconfig(
        tmp_path / "tsconfig.json",
        {"compilerOptions": {"checkJs": True}},
    )
    assert_that(enables_check_js(path)).is_true()


def test_enables_check_js_false(tmp_path: Path) -> None:
    """Explicit checkJs: false is not treated as enabled.

    Args:
        tmp_path: Pytest temporary directory.
    """
    path = write_tsconfig(
        tmp_path / "tsconfig.json",
        {"compilerOptions": {"checkJs": False, "allowJs": True}},
    )
    assert_that(enables_check_js(path)).is_false()


def test_enables_check_js_unset(tmp_path: Path) -> None:
    """Missing checkJs is not enabled.

    Args:
        tmp_path: Pytest temporary directory.
    """
    path = write_tsconfig(
        tmp_path / "tsconfig.json",
        {"compilerOptions": {"strict": True}},
    )
    assert_that(enables_check_js(path)).is_false()


def test_enables_check_js_via_extends(tmp_path: Path) -> None:
    """Inherited checkJs from an extended base config is detected.

    Args:
        tmp_path: Pytest temporary directory.
    """
    write_tsconfig(
        tmp_path / "tsconfig.base.json",
        {"compilerOptions": {"checkJs": True}},
    )
    path = write_tsconfig(
        tmp_path / "tsconfig.json",
        {"extends": "./tsconfig.base.json"},
    )
    assert_that(enables_check_js(path)).is_true()


def test_enables_check_js_child_overrides_parent(tmp_path: Path) -> None:
    """Child checkJs: false overrides a parent checkJs: true.

    Args:
        tmp_path: Pytest temporary directory.
    """
    write_tsconfig(
        tmp_path / "tsconfig.base.json",
        {"compilerOptions": {"checkJs": True}},
    )
    path = write_tsconfig(
        tmp_path / "tsconfig.json",
        {
            "extends": "./tsconfig.base.json",
            "compilerOptions": {"checkJs": False},
        },
    )
    assert_that(enables_check_js(path)).is_false()

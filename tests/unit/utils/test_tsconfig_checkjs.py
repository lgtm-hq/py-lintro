"""Unit tests for tsconfig checkJs resolution (issue #1185)."""

from __future__ import annotations

from pathlib import Path

from assertpy import assert_that

from lintro.utils.tsconfig import enables_check_js, resolve_extends_chain
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


def test_enables_check_js_via_array_extends(tmp_path: Path) -> None:
    """Array extends merges compilerOptions; later parents override earlier.

    Args:
        tmp_path: Pytest temporary directory.
    """
    write_tsconfig(
        tmp_path / "base1.json",
        {"compilerOptions": {"checkJs": False, "strict": True}},
    )
    write_tsconfig(
        tmp_path / "base2.json",
        {"compilerOptions": {"checkJs": True}},
    )
    path = write_tsconfig(
        tmp_path / "tsconfig.json",
        {"extends": ["./base1.json", "./base2.json"]},
    )
    info = resolve_extends_chain(path)
    assert_that(enables_check_js(path)).is_true()
    assert_that(info.compiler_options.get("checkJs")).is_true()
    assert_that(info.compiler_options.get("strict")).is_true()
    assert_that(info.unresolved_extends).is_false()


def test_enables_check_js_array_extends_later_false_wins(tmp_path: Path) -> None:
    """A later array-extends parent can disable an earlier checkJs: true.

    Args:
        tmp_path: Pytest temporary directory.
    """
    write_tsconfig(
        tmp_path / "base1.json",
        {"compilerOptions": {"checkJs": True}},
    )
    write_tsconfig(
        tmp_path / "base2.json",
        {"compilerOptions": {"checkJs": False}},
    )
    path = write_tsconfig(
        tmp_path / "tsconfig.json",
        {"extends": ["./base1.json", "./base2.json"]},
    )
    assert_that(enables_check_js(path)).is_false()


def test_unresolved_extends_is_flagged(tmp_path: Path) -> None:
    """Missing extends targets set unresolved_extends and do not enable checkJs.

    Args:
        tmp_path: Pytest temporary directory.
    """
    path = write_tsconfig(
        tmp_path / "tsconfig.json",
        {"extends": "@tsconfig/strictest/tsconfig.json"},
    )
    info = resolve_extends_chain(path)
    assert_that(info.unresolved_extends).is_true()
    assert_that(enables_check_js(path)).is_false()


def test_unresolved_array_extends_is_flagged(tmp_path: Path) -> None:
    """A missing entry in array extends still flags the chain unresolved.

    Args:
        tmp_path: Pytest temporary directory.
    """
    write_tsconfig(
        tmp_path / "base.json",
        {"compilerOptions": {"strict": True}},
    )
    path = write_tsconfig(
        tmp_path / "tsconfig.json",
        {
            "extends": ["./base.json", "./missing.json"],
            "compilerOptions": {"checkJs": True},
        },
    )
    info = resolve_extends_chain(path)
    assert_that(info.unresolved_extends).is_true()
    assert_that(enables_check_js(path)).is_true()

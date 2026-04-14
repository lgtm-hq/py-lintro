"""Unit tests for resolve_extends_chain."""

from __future__ import annotations

from pathlib import Path

from assertpy import assert_that

from lintro.utils.tsconfig import resolve_extends_chain
from tests.unit.utils.tsconfig_helpers import write_tsconfig


def test_resolve_single_level_extends(tmp_path: Path) -> None:
    """Child inherits include from parent via extends."""
    write_tsconfig(
        tmp_path / "tsconfig.base.json",
        {"include": ["src/**/*.ts"], "exclude": ["dist"]},
    )
    write_tsconfig(
        tmp_path / "tsconfig.json",
        {"extends": "./tsconfig.base.json", "compilerOptions": {"strict": True}},
    )
    info = resolve_extends_chain(tmp_path / "tsconfig.json")
    assert_that(info.include_patterns).is_equal_to(["src/**/*.ts"])
    assert_that(info.exclude_patterns).is_equal_to(["dist"])


def test_resolve_child_overrides_parent(tmp_path: Path) -> None:
    """Child's include overrides parent's include."""
    write_tsconfig(
        tmp_path / "tsconfig.base.json",
        {"include": ["src/**/*.ts"]},
    )
    write_tsconfig(
        tmp_path / "tsconfig.json",
        {"extends": "./tsconfig.base.json", "include": ["lib/**/*.ts"]},
    )
    info = resolve_extends_chain(tmp_path / "tsconfig.json")
    assert_that(info.include_patterns).is_equal_to(["lib/**/*.ts"])


def test_resolve_multi_level_extends(tmp_path: Path) -> None:
    """Three-level chain: grandparent → parent → child."""
    write_tsconfig(
        tmp_path / "tsconfig.grandparent.json",
        {"include": ["src/**/*.ts"], "exclude": ["test"]},
    )
    write_tsconfig(
        tmp_path / "tsconfig.base.json",
        {"extends": "./tsconfig.grandparent.json", "exclude": ["dist"]},
    )
    write_tsconfig(
        tmp_path / "tsconfig.json",
        {"extends": "./tsconfig.base.json"},
    )
    info = resolve_extends_chain(tmp_path / "tsconfig.json")
    # include from grandparent (not overridden)
    assert_that(info.include_patterns).is_equal_to(["src/**/*.ts"])
    # exclude from parent (overrides grandparent)
    assert_that(info.exclude_patterns).is_equal_to(["dist"])


def test_resolve_circular_extends(tmp_path: Path) -> None:
    """Circular extends does not infinite loop."""
    write_tsconfig(
        tmp_path / "a.json",
        {"extends": "./b.json", "include": ["src/**/*.ts"]},
    )
    write_tsconfig(
        tmp_path / "b.json",
        {"extends": "./a.json"},
    )
    # Should not hang — cycle detection kicks in
    info = resolve_extends_chain(tmp_path / "a.json")
    assert_that(info.include_patterns).is_equal_to(["src/**/*.ts"])


def test_resolve_missing_extends_target(tmp_path: Path) -> None:
    """Missing extends target is silently skipped."""
    write_tsconfig(
        tmp_path / "tsconfig.json",
        {"extends": "./nonexistent.json", "include": ["src/**/*.ts"]},
    )
    info = resolve_extends_chain(tmp_path / "tsconfig.json")
    assert_that(info.include_patterns).is_equal_to(["src/**/*.ts"])


def test_resolve_array_extends_ts5(tmp_path: Path) -> None:
    """TS 5.0+ array extends merges in order, child overrides all."""
    write_tsconfig(
        tmp_path / "base1.json",
        {"include": ["a/**/*.ts"]},
    )
    write_tsconfig(
        tmp_path / "base2.json",
        {"include": ["b/**/*.ts"]},
    )
    write_tsconfig(
        tmp_path / "tsconfig.json",
        {"extends": ["./base1.json", "./base2.json"]},
    )
    info = resolve_extends_chain(tmp_path / "tsconfig.json")
    # base2 overrides base1; child has no include so inherits from base2
    assert_that(info.include_patterns).is_equal_to(["b/**/*.ts"])

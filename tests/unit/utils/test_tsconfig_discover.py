"""Unit tests for discover_tsconfigs."""

from __future__ import annotations

from pathlib import Path

from assertpy import assert_that

from lintro.utils.tsconfig import discover_tsconfigs
from tests.unit.utils.tsconfig_helpers import write_tsconfig


def test_discover_single_tsconfig(tmp_path: Path) -> None:
    """Single tsconfig at root returns one result."""
    write_tsconfig(
        tmp_path / "tsconfig.json",
        {"include": ["src/**/*.ts"]},
    )
    result = discover_tsconfigs(tmp_path)
    assert_that(result).is_length(1)
    assert_that(result[0].path).is_equal_to((tmp_path / "tsconfig.json").resolve())


def test_discover_with_references(tmp_path: Path) -> None:
    """Root tsconfig with references discovers sub-projects."""
    write_tsconfig(
        tmp_path / "packages" / "api" / "tsconfig.json",
        {"include": ["src/**/*.ts"], "compilerOptions": {"composite": True}},
    )
    write_tsconfig(
        tmp_path / "packages" / "web" / "tsconfig.json",
        {"include": ["src/**/*.ts"], "compilerOptions": {"composite": True}},
    )
    write_tsconfig(
        tmp_path / "tsconfig.json",
        {
            "references": [
                {"path": "./packages/api"},
                {"path": "./packages/web"},
            ],
        },
    )
    result = discover_tsconfigs(tmp_path)
    # Should find root + 2 sub-projects = 3
    assert_that(result).is_length(3)
    project_dirs = {str(info.project_dir) for info in result}
    assert_that(project_dirs).contains(str((tmp_path / "packages" / "api").resolve()))
    assert_that(project_dirs).contains(str((tmp_path / "packages" / "web").resolve()))


def test_discover_no_root_tsconfig(tmp_path: Path) -> None:
    """Tsconfigs only in subdirs are found via tree walk."""
    write_tsconfig(
        tmp_path / "packages" / "api" / "tsconfig.json",
        {"include": ["src/**/*.ts"]},
    )
    write_tsconfig(
        tmp_path / "packages" / "web" / "tsconfig.json",
        {"include": ["src/**/*.ts"]},
    )
    result = discover_tsconfigs(tmp_path)
    assert_that(result).is_length(2)


def test_discover_skips_node_modules(tmp_path: Path) -> None:
    """Tsconfigs inside node_modules are not discovered."""
    write_tsconfig(
        tmp_path / "tsconfig.json",
        {"include": ["src/**/*.ts"]},
    )
    write_tsconfig(
        tmp_path / "node_modules" / "some-lib" / "tsconfig.json",
        {"include": ["lib/**/*.ts"]},
    )
    result = discover_tsconfigs(tmp_path)
    assert_that(result).is_length(1)


def test_discover_filters_non_checking_configs(tmp_path: Path) -> None:
    """tsconfig.build.json excluded unless found via references."""
    write_tsconfig(
        tmp_path / "tsconfig.json",
        {"include": ["src/**/*.ts"]},
    )
    write_tsconfig(
        tmp_path / "tsconfig.build.json",
        {"include": ["src/**/*.ts"], "compilerOptions": {"outDir": "dist"}},
    )
    result = discover_tsconfigs(tmp_path)
    # Only tsconfig.json, not tsconfig.build.json
    assert_that(result).is_length(1)
    assert_that(result[0].path.name).is_equal_to("tsconfig.json")


def test_discover_includes_referenced_non_checking_config(tmp_path: Path) -> None:
    """tsconfig.node.json IS included when found via references."""
    write_tsconfig(
        tmp_path / "tsconfig.node.json",
        {"include": ["vite.config.ts"]},
    )
    write_tsconfig(
        tmp_path / "tsconfig.json",
        {"references": [{"path": "./tsconfig.node.json"}]},
    )
    result = discover_tsconfigs(tmp_path)
    names = {info.path.name for info in result}
    assert_that(names).contains("tsconfig.node.json")


def test_discover_deduplicates(tmp_path: Path) -> None:
    """Config found by both references and walk is not duplicated."""
    write_tsconfig(
        tmp_path / "packages" / "api" / "tsconfig.json",
        {"include": ["src/**/*.ts"]},
    )
    write_tsconfig(
        tmp_path / "tsconfig.json",
        {"references": [{"path": "./packages/api"}]},
    )
    result = discover_tsconfigs(tmp_path)
    paths = [str(info.path) for info in result]
    # Should have root + packages/api, no duplicates
    assert_that(len(paths)).is_equal_to(len(set(paths)))


def test_discover_circular_references(tmp_path: Path) -> None:
    """Circular references don't cause infinite loop."""
    write_tsconfig(
        tmp_path / "a" / "tsconfig.json",
        {"references": [{"path": "../b"}]},
    )
    write_tsconfig(
        tmp_path / "b" / "tsconfig.json",
        {"references": [{"path": "../a"}]},
    )
    write_tsconfig(
        tmp_path / "tsconfig.json",
        {"references": [{"path": "./a"}]},
    )
    # Should complete without hanging
    result = discover_tsconfigs(tmp_path)
    assert_that(result).is_not_empty()

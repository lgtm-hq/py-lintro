"""Unit tests for partition_files, has_explicit_scoping, and create_temp_tsconfig."""

from __future__ import annotations

import json
from pathlib import Path

from assertpy import assert_that

from lintro.utils.tsconfig import (
    TsconfigInfo,
    create_temp_tsconfig,
    has_explicit_scoping,
    partition_files,
)
from tests.unit.utils.tsconfig_helpers import write_tsconfig

# =============================================================================
# Tests for partition_files
# =============================================================================


def test_partition_deepest_wins(tmp_path: Path) -> None:
    """Files go to deepest tsconfig whose project_dir contains them."""
    parent_info = TsconfigInfo(
        path=(tmp_path / "tsconfig.json").resolve(),
        project_dir=tmp_path.resolve(),
    )
    child_info = TsconfigInfo(
        path=(tmp_path / "packages" / "api" / "tsconfig.json").resolve(),
        project_dir=(tmp_path / "packages" / "api").resolve(),
    )
    # deepest-first order
    tsconfigs = [child_info, parent_info]

    child_file = str((tmp_path / "packages" / "api" / "src" / "index.ts").resolve())
    root_file = str((tmp_path / "utils.ts").resolve())
    files = [child_file, root_file]

    result = partition_files(files, tsconfigs)

    child_partition = next(
        (files for info, files in result if info is child_info),
        [],
    )
    assert_that(child_partition).contains(child_file)
    assert_that(child_partition).does_not_contain(root_file)

    parent_partition = next(
        (files for info, files in result if info is parent_info),
        [],
    )
    assert_that(parent_partition).contains(root_file)
    assert_that(parent_partition).does_not_contain(child_file)


def test_partition_fallback_group(tmp_path: Path) -> None:
    """Files not under any tsconfig go to the None fallback group."""
    child_info = TsconfigInfo(
        path=(tmp_path / "packages" / "api" / "tsconfig.json").resolve(),
        project_dir=(tmp_path / "packages" / "api").resolve(),
    )
    tsconfigs = [child_info]

    orphan_file = str((tmp_path / "scripts" / "deploy.ts").resolve())
    result = partition_files([orphan_file], tsconfigs)

    fallback = next(
        (files for info, files in result if info is None),
        [],
    )
    assert_that(fallback).contains(orphan_file)


def test_partition_empty_sub_project(tmp_path: Path) -> None:
    """Sub-project with no matching files gets an empty list."""
    info = TsconfigInfo(
        path=(tmp_path / "packages" / "api" / "tsconfig.json").resolve(),
        project_dir=(tmp_path / "packages" / "api").resolve(),
    )
    tsconfigs = [info]

    file_elsewhere = str((tmp_path / "other" / "app.ts").resolve())
    result = partition_files([file_elsewhere], tsconfigs)

    api_partition = next(
        (files for i, files in result if i is info),
        [],
    )
    assert_that(api_partition).is_empty()


# =============================================================================
# Tests for has_explicit_scoping
# =============================================================================


def test_has_explicit_scoping_with_include() -> None:
    """Returns True when include is non-empty."""
    info = TsconfigInfo(
        path=Path("/fake/tsconfig.json"),
        project_dir=Path("/fake"),
        include_patterns=["src/**/*.ts"],
    )
    assert_that(has_explicit_scoping(info)).is_true()


def test_has_explicit_scoping_with_files() -> None:
    """Returns True when files is non-empty."""
    info = TsconfigInfo(
        path=Path("/fake/tsconfig.json"),
        project_dir=Path("/fake"),
        files_list=["index.ts"],
    )
    assert_that(has_explicit_scoping(info)).is_true()


def test_has_explicit_scoping_with_exclude() -> None:
    """Returns True when exclude is non-empty (clearing it would lose scoping)."""
    info = TsconfigInfo(
        path=Path("/fake/tsconfig.json"),
        project_dir=Path("/fake"),
        exclude_patterns=["vitest.config.ts"],
    )
    assert_that(has_explicit_scoping(info)).is_true()


def test_has_explicit_scoping_with_neither() -> None:
    """Returns False when include, files, and exclude are all empty."""
    info = TsconfigInfo(
        path=Path("/fake/tsconfig.json"),
        project_dir=Path("/fake"),
    )
    assert_that(has_explicit_scoping(info)).is_false()


def test_has_explicit_scoping_with_both() -> None:
    """Returns True when both include and files are set."""
    info = TsconfigInfo(
        path=Path("/fake/tsconfig.json"),
        project_dir=Path("/fake"),
        include_patterns=["src/**/*.ts"],
        files_list=["globals.d.ts"],
    )
    assert_that(has_explicit_scoping(info)).is_true()


# =============================================================================
# Tests for create_temp_tsconfig
# =============================================================================


def test_create_temp_tsconfig_extends_base(tmp_path: Path) -> None:
    """Temp tsconfig extends the base config."""
    base = write_tsconfig(
        tmp_path / "tsconfig.json",
        {"compilerOptions": {"strict": True}},
    )
    temp = create_temp_tsconfig(base, ["src/app.ts"], tmp_path)
    try:
        content = json.loads(temp.read_text(encoding="utf-8"))
        assert_that(content["extends"]).is_equal_to(str(base.resolve()))
    finally:
        temp.unlink(missing_ok=True)


def test_create_temp_tsconfig_includes_files(tmp_path: Path) -> None:
    """Temp tsconfig includes only the specified files."""
    base = write_tsconfig(
        tmp_path / "tsconfig.json",
        {"compilerOptions": {"strict": True}},
    )
    temp = create_temp_tsconfig(base, ["src/a.ts", "src/b.ts"], tmp_path)
    try:
        content = json.loads(temp.read_text(encoding="utf-8"))
        assert_that(content["include"]).is_length(2)
    finally:
        temp.unlink(missing_ok=True)


def test_create_temp_tsconfig_sets_no_emit(tmp_path: Path) -> None:
    """Temp tsconfig always sets noEmit: true."""
    base = write_tsconfig(
        tmp_path / "tsconfig.json",
        {"compilerOptions": {}},
    )
    temp = create_temp_tsconfig(base, ["app.ts"], tmp_path)
    try:
        content = json.loads(temp.read_text(encoding="utf-8"))
        assert_that(content["compilerOptions"]["noEmit"]).is_true()
    finally:
        temp.unlink(missing_ok=True)


def test_create_temp_tsconfig_preserves_type_roots(tmp_path: Path) -> None:
    """Temp tsconfig preserves typeRoots from base config."""
    type_root = str(tmp_path / "custom-types")
    base = write_tsconfig(
        tmp_path / "tsconfig.json",
        {"compilerOptions": {"typeRoots": [type_root]}},
    )
    temp = create_temp_tsconfig(base, ["app.ts"], tmp_path)
    try:
        content = json.loads(temp.read_text(encoding="utf-8"))
        assert_that(content["compilerOptions"]["typeRoots"]).is_not_empty()
    finally:
        temp.unlink(missing_ok=True)


def test_create_temp_tsconfig_custom_prefix(tmp_path: Path) -> None:
    """Custom prefix is used in the temp filename."""
    base = write_tsconfig(
        tmp_path / "tsconfig.json",
        {"compilerOptions": {}},
    )
    temp = create_temp_tsconfig(
        base,
        ["app.ts"],
        tmp_path,
        prefix=".lintro-vue-tsc-",
        tool_label="vue-tsc",
    )
    try:
        assert_that(temp.name).starts_with(".lintro-vue-tsc-")
    finally:
        temp.unlink(missing_ok=True)

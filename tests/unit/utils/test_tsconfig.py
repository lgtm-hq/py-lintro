"""Unit tests for lintro.utils.tsconfig module."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from assertpy import assert_that

from lintro.utils.tsconfig import (
    TsconfigInfo,
    create_temp_tsconfig,
    discover_tsconfigs,
    has_explicit_scoping,
    parse_tsconfig,
    partition_files,
    resolve_extends_chain,
)

# =============================================================================
# Helpers
# =============================================================================


def _write_tsconfig(path: Path, content: dict[str, Any]) -> Path:
    """Write a tsconfig.json (or any .json) to *path*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(content, indent=2), encoding="utf-8")
    return path


# =============================================================================
# Tests for parse_tsconfig
# =============================================================================


def test_parse_basic_tsconfig(tmp_path: Path) -> None:
    """Parse a tsconfig with include, exclude, and compilerOptions."""
    tsconfig = _write_tsconfig(
        tmp_path / "tsconfig.json",
        {
            "compilerOptions": {"strict": True, "composite": True},
            "include": ["src/**/*.ts"],
            "exclude": ["node_modules"],
            "files": ["globals.d.ts"],
        },
    )
    info = parse_tsconfig(tsconfig)
    assert_that(info.include_patterns).is_equal_to(["src/**/*.ts"])
    assert_that(info.exclude_patterns).is_equal_to(["node_modules"])
    assert_that(info.files_list).is_equal_to(["globals.d.ts"])
    assert_that(info.is_composite).is_true()
    assert_that(info.project_dir).is_equal_to(tmp_path.resolve())


def test_parse_tsconfig_no_optional_fields(tmp_path: Path) -> None:
    """Parse a tsconfig with only compilerOptions."""
    tsconfig = _write_tsconfig(
        tmp_path / "tsconfig.json",
        {"compilerOptions": {"strict": True}},
    )
    info = parse_tsconfig(tsconfig)
    assert_that(info.include_patterns).is_empty()
    assert_that(info.exclude_patterns).is_empty()
    assert_that(info.files_list).is_empty()
    assert_that(info.references).is_empty()
    assert_that(info.is_composite).is_false()


def test_parse_tsconfig_with_jsonc_comments(tmp_path: Path) -> None:
    """Parse a tsconfig that uses JSONC comments and trailing commas."""
    tsconfig_path = tmp_path / "tsconfig.json"
    tsconfig_path.write_text(
        '{\n  // This is a comment\n  "include": ["src/**/*.ts"],\n'
        '  "compilerOptions": {\n    "strict": true,\n  },\n}\n',
        encoding="utf-8",
    )
    info = parse_tsconfig(tsconfig_path)
    assert_that(info.include_patterns).is_equal_to(["src/**/*.ts"])


def test_parse_tsconfig_with_references(tmp_path: Path) -> None:
    """Parse a tsconfig with project references."""
    # Create referenced projects
    _write_tsconfig(
        tmp_path / "packages" / "api" / "tsconfig.json",
        {"compilerOptions": {"strict": True}},
    )
    _write_tsconfig(
        tmp_path / "packages" / "web" / "tsconfig.json",
        {"compilerOptions": {"strict": True}},
    )
    tsconfig = _write_tsconfig(
        tmp_path / "tsconfig.json",
        {
            "references": [
                {"path": "./packages/api"},
                {"path": "./packages/web"},
            ],
        },
    )
    info = parse_tsconfig(tsconfig)
    assert_that(info.references).is_length(2)


def test_parse_tsconfig_malformed_file(tmp_path: Path) -> None:
    """Return empty info for a malformed tsconfig."""
    tsconfig_path = tmp_path / "tsconfig.json"
    tsconfig_path.write_text("not valid json", encoding="utf-8")
    info = parse_tsconfig(tsconfig_path)
    assert_that(info.include_patterns).is_empty()
    assert_that(info.raw_config).is_empty()


def test_parse_tsconfig_non_dict_content(tmp_path: Path) -> None:
    """Return empty info when tsconfig content is not a dict."""
    tsconfig_path = tmp_path / "tsconfig.json"
    tsconfig_path.write_text('["not", "a", "dict"]', encoding="utf-8")
    info = parse_tsconfig(tsconfig_path)
    assert_that(info.include_patterns).is_empty()


# =============================================================================
# Tests for resolve_extends_chain
# =============================================================================


def test_resolve_single_level_extends(tmp_path: Path) -> None:
    """Child inherits include from parent via extends."""
    _write_tsconfig(
        tmp_path / "tsconfig.base.json",
        {"include": ["src/**/*.ts"], "exclude": ["dist"]},
    )
    _write_tsconfig(
        tmp_path / "tsconfig.json",
        {"extends": "./tsconfig.base.json", "compilerOptions": {"strict": True}},
    )
    info = resolve_extends_chain(tmp_path / "tsconfig.json")
    assert_that(info.include_patterns).is_equal_to(["src/**/*.ts"])
    assert_that(info.exclude_patterns).is_equal_to(["dist"])


def test_resolve_child_overrides_parent(tmp_path: Path) -> None:
    """Child's include overrides parent's include."""
    _write_tsconfig(
        tmp_path / "tsconfig.base.json",
        {"include": ["src/**/*.ts"]},
    )
    _write_tsconfig(
        tmp_path / "tsconfig.json",
        {"extends": "./tsconfig.base.json", "include": ["lib/**/*.ts"]},
    )
    info = resolve_extends_chain(tmp_path / "tsconfig.json")
    assert_that(info.include_patterns).is_equal_to(["lib/**/*.ts"])


def test_resolve_multi_level_extends(tmp_path: Path) -> None:
    """Three-level chain: grandparent → parent → child."""
    _write_tsconfig(
        tmp_path / "tsconfig.grandparent.json",
        {"include": ["src/**/*.ts"], "exclude": ["test"]},
    )
    _write_tsconfig(
        tmp_path / "tsconfig.base.json",
        {"extends": "./tsconfig.grandparent.json", "exclude": ["dist"]},
    )
    _write_tsconfig(
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
    _write_tsconfig(
        tmp_path / "a.json",
        {"extends": "./b.json", "include": ["src/**/*.ts"]},
    )
    _write_tsconfig(
        tmp_path / "b.json",
        {"extends": "./a.json"},
    )
    # Should not hang — cycle detection kicks in
    info = resolve_extends_chain(tmp_path / "a.json")
    assert_that(info.include_patterns).is_equal_to(["src/**/*.ts"])


def test_resolve_missing_extends_target(tmp_path: Path) -> None:
    """Missing extends target is silently skipped."""
    _write_tsconfig(
        tmp_path / "tsconfig.json",
        {"extends": "./nonexistent.json", "include": ["src/**/*.ts"]},
    )
    info = resolve_extends_chain(tmp_path / "tsconfig.json")
    assert_that(info.include_patterns).is_equal_to(["src/**/*.ts"])


def test_resolve_array_extends_ts5(tmp_path: Path) -> None:
    """TS 5.0+ array extends merges in order, child overrides all."""
    _write_tsconfig(
        tmp_path / "base1.json",
        {"include": ["a/**/*.ts"]},
    )
    _write_tsconfig(
        tmp_path / "base2.json",
        {"include": ["b/**/*.ts"]},
    )
    _write_tsconfig(
        tmp_path / "tsconfig.json",
        {"extends": ["./base1.json", "./base2.json"]},
    )
    info = resolve_extends_chain(tmp_path / "tsconfig.json")
    # base2 overrides base1, child has no include so inherits from base2
    assert_that(info.include_patterns).is_equal_to(["b/**/*.ts"])


# =============================================================================
# Tests for discover_tsconfigs
# =============================================================================


def test_discover_single_tsconfig(tmp_path: Path) -> None:
    """Single tsconfig at root returns one result."""
    _write_tsconfig(
        tmp_path / "tsconfig.json",
        {"include": ["src/**/*.ts"]},
    )
    result = discover_tsconfigs(tmp_path)
    assert_that(result).is_length(1)
    assert_that(result[0].path).is_equal_to((tmp_path / "tsconfig.json").resolve())


def test_discover_with_references(tmp_path: Path) -> None:
    """Root tsconfig with references discovers sub-projects."""
    _write_tsconfig(
        tmp_path / "packages" / "api" / "tsconfig.json",
        {"include": ["src/**/*.ts"], "compilerOptions": {"composite": True}},
    )
    _write_tsconfig(
        tmp_path / "packages" / "web" / "tsconfig.json",
        {"include": ["src/**/*.ts"], "compilerOptions": {"composite": True}},
    )
    _write_tsconfig(
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
    _write_tsconfig(
        tmp_path / "packages" / "api" / "tsconfig.json",
        {"include": ["src/**/*.ts"]},
    )
    _write_tsconfig(
        tmp_path / "packages" / "web" / "tsconfig.json",
        {"include": ["src/**/*.ts"]},
    )
    result = discover_tsconfigs(tmp_path)
    assert_that(result).is_length(2)


def test_discover_skips_node_modules(tmp_path: Path) -> None:
    """Tsconfigs inside node_modules are not discovered."""
    _write_tsconfig(
        tmp_path / "tsconfig.json",
        {"include": ["src/**/*.ts"]},
    )
    _write_tsconfig(
        tmp_path / "node_modules" / "some-lib" / "tsconfig.json",
        {"include": ["lib/**/*.ts"]},
    )
    result = discover_tsconfigs(tmp_path)
    assert_that(result).is_length(1)


def test_discover_filters_non_checking_configs(tmp_path: Path) -> None:
    """tsconfig.build.json excluded unless found via references."""
    _write_tsconfig(
        tmp_path / "tsconfig.json",
        {"include": ["src/**/*.ts"]},
    )
    _write_tsconfig(
        tmp_path / "tsconfig.build.json",
        {"include": ["src/**/*.ts"], "compilerOptions": {"outDir": "dist"}},
    )
    result = discover_tsconfigs(tmp_path)
    # Only tsconfig.json, not tsconfig.build.json
    assert_that(result).is_length(1)
    assert_that(result[0].path.name).is_equal_to("tsconfig.json")


def test_discover_includes_referenced_non_checking_config(tmp_path: Path) -> None:
    """tsconfig.node.json IS included when found via references."""
    _write_tsconfig(
        tmp_path / "tsconfig.node.json",
        {"include": ["vite.config.ts"]},
    )
    _write_tsconfig(
        tmp_path / "tsconfig.json",
        {"references": [{"path": "./tsconfig.node.json"}]},
    )
    result = discover_tsconfigs(tmp_path)
    names = {info.path.name for info in result}
    assert_that(names).contains("tsconfig.node.json")


def test_discover_deduplicates(tmp_path: Path) -> None:
    """Config found by both references and walk is not duplicated."""
    _write_tsconfig(
        tmp_path / "packages" / "api" / "tsconfig.json",
        {"include": ["src/**/*.ts"]},
    )
    _write_tsconfig(
        tmp_path / "tsconfig.json",
        {"references": [{"path": "./packages/api"}]},
    )
    result = discover_tsconfigs(tmp_path)
    paths = [str(info.path) for info in result]
    # Should have root + packages/api, no duplicates
    assert_that(len(paths)).is_equal_to(len(set(paths)))


def test_discover_circular_references(tmp_path: Path) -> None:
    """Circular references don't cause infinite loop."""
    _write_tsconfig(
        tmp_path / "a" / "tsconfig.json",
        {"references": [{"path": "../b"}]},
    )
    _write_tsconfig(
        tmp_path / "b" / "tsconfig.json",
        {"references": [{"path": "../a"}]},
    )
    _write_tsconfig(
        tmp_path / "tsconfig.json",
        {"references": [{"path": "./a"}]},
    )
    # Should complete without hanging
    result = discover_tsconfigs(tmp_path)
    assert_that(result).is_not_empty()


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

    # child_info gets the child_file
    child_partition = next(
        (files for info, files in result if info is child_info),
        [],
    )
    assert_that(child_partition).contains(child_file)
    assert_that(child_partition).does_not_contain(root_file)

    # parent_info gets the root_file
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
    files = [orphan_file]

    result = partition_files(files, tsconfigs)

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

    # No files are under packages/api
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


def test_has_explicit_scoping_with_neither() -> None:
    """Returns False when both include and files are empty."""
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
    base = _write_tsconfig(
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
    base = _write_tsconfig(
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
    base = _write_tsconfig(
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
    base = _write_tsconfig(
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
    base = _write_tsconfig(
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

"""Unit tests for parse_tsconfig."""

from __future__ import annotations

from pathlib import Path

from assertpy import assert_that

from lintro.utils.tsconfig import parse_tsconfig
from tests.unit.utils.tsconfig_helpers import write_tsconfig


def test_parse_basic_tsconfig(tmp_path: Path) -> None:
    """Parse a tsconfig with include, exclude, and compilerOptions."""
    tsconfig = write_tsconfig(
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
    tsconfig = write_tsconfig(
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
    write_tsconfig(
        tmp_path / "packages" / "api" / "tsconfig.json",
        {"compilerOptions": {"strict": True}},
    )
    write_tsconfig(
        tmp_path / "packages" / "web" / "tsconfig.json",
        {"compilerOptions": {"strict": True}},
    )
    tsconfig = write_tsconfig(
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

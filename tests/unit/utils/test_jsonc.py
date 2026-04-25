"""Unit tests for lintro.utils.jsonc module."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.utils.jsonc import (
    extract_tsconfig_fields,
    load_jsonc,
    strip_jsonc_comments,
    strip_trailing_commas,
)

# =============================================================================
# Tests for strip_jsonc_comments
# =============================================================================


def test_strip_jsonc_comments_no_comments() -> None:
    """Return unchanged content when no comments are present."""
    content = '{"key": "value", "num": 42}'
    result = strip_jsonc_comments(content)
    assert_that(result).is_equal_to(content)


def test_strip_jsonc_comments_line_comment() -> None:
    """Strip single-line // comments."""
    content = '{"key": "value"} // this is a comment'
    result = strip_jsonc_comments(content)
    assert_that(result.strip()).is_equal_to('{"key": "value"}')


def test_strip_jsonc_comments_block_comment() -> None:
    """Strip /* */ block comments."""
    content = '{"key": /* comment */ "value"}'
    result = strip_jsonc_comments(content)
    parsed = json.loads(result)
    assert_that(parsed["key"]).is_equal_to("value")


def test_strip_jsonc_comments_preserves_strings() -> None:
    """Preserve // and /* patterns inside string values."""
    content = '{"url": "https://example.com"}'
    result = strip_jsonc_comments(content)
    parsed = json.loads(result)
    assert_that(parsed["url"]).is_equal_to("https://example.com")


# =============================================================================
# Tests for strip_trailing_commas
# =============================================================================


def test_strip_trailing_commas_removes_trailing_comma_before_brace() -> None:
    """Remove trailing comma before closing brace."""
    content = '{"a": 1, "b": 2,}'
    result = strip_trailing_commas(content)
    assert_that(result).is_equal_to('{"a": 1, "b": 2}')


def test_strip_trailing_commas_removes_trailing_comma_before_bracket() -> None:
    """Remove trailing comma before closing bracket."""
    content = '["a", "b",]'
    result = strip_trailing_commas(content)
    assert_that(result).is_equal_to('["a", "b"]')


def test_strip_trailing_commas_handles_whitespace() -> None:
    """Remove trailing comma with whitespace before closing."""
    content = '{"a": 1,\n}'
    result = strip_trailing_commas(content)
    assert_that(result).is_equal_to('{"a": 1\n}')


def test_strip_trailing_commas_no_trailing_comma() -> None:
    """Return unchanged content when no trailing commas."""
    content = '{"a": 1, "b": 2}'
    result = strip_trailing_commas(content)
    assert_that(result).is_equal_to(content)


# =============================================================================
# Tests for load_jsonc
# =============================================================================


def test_load_jsonc_plain_json() -> None:
    """Parse plain JSON without comments or trailing commas."""
    result = load_jsonc('{"key": "value"}')
    assert_that(result).is_equal_to({"key": "value"})


def test_load_jsonc_with_comments() -> None:
    """Parse JSONC with line and block comments."""
    content = """{
  // A line comment
  "name": "test",
  /* block comment */
  "value": 42
}"""
    result = load_jsonc(content)
    assert_that(result["name"]).is_equal_to("test")
    assert_that(result["value"]).is_equal_to(42)


def test_load_jsonc_with_trailing_commas() -> None:
    """Parse JSONC with trailing commas."""
    content = '{"a": 1, "b": [1, 2, 3,],}'
    result = load_jsonc(content)
    assert_that(result["a"]).is_equal_to(1)
    assert_that(result["b"]).is_equal_to([1, 2, 3])


def test_load_jsonc_with_comments_and_trailing_commas() -> None:
    """Parse JSONC with both comments and trailing commas."""
    content = """{
  // compiler settings
  "compilerOptions": {
    "strict": true, // enable strict mode
    "typeRoots": [
      "./custom-types",
      "./node_modules/@types",
    ],
  },
}"""
    result = load_jsonc(content)
    assert_that(result["compilerOptions"]["strict"]).is_true()
    assert_that(result["compilerOptions"]["typeRoots"]).is_equal_to(
        ["./custom-types", "./node_modules/@types"],
    )


def test_load_jsonc_invalid_json_raises() -> None:
    """Raise JSONDecodeError for invalid JSON after stripping."""
    with pytest.raises(json.JSONDecodeError):
        load_jsonc("{invalid}")


def test_load_jsonc_tsconfig_with_comments_and_type_roots() -> None:
    """Parse a realistic tsconfig.json with JSONC features and typeRoots.

    This is the primary scenario from issue #570.
    """
    content = """{
  // TypeScript configuration
  "compilerOptions": {
    "target": "ES2020",
    "module": "ESNext",
    "strict": true,
    /* Custom type roots for monorepo */
    "typeRoots": [
      "./types",
      "./node_modules/@types",
    ],
    "outDir": "./dist",
  },
  "include": ["src/**/*.ts"],
  "exclude": ["node_modules"],
}"""
    result = load_jsonc(content)
    assert_that(result["compilerOptions"]["typeRoots"]).is_equal_to(
        ["./types", "./node_modules/@types"],
    )
    assert_that(result["compilerOptions"]["strict"]).is_true()


# =============================================================================
# Tests for extract_tsconfig_fields
# =============================================================================


def test_extract_tsconfig_fields_full(tmp_path: Path) -> None:
    """Extract all fields from a complete tsconfig dict."""
    (tmp_path / "packages" / "api").mkdir(parents=True)
    (tmp_path / "packages" / "api" / "tsconfig.json").write_text("{}")

    content = {
        "include": ["src/**/*.ts"],
        "exclude": ["dist"],
        "files": ["globals.d.ts"],
        "extends": "./base.json",
        "compilerOptions": {"composite": True},
        "references": [{"path": "./packages/api"}],
    }
    result = extract_tsconfig_fields(content, tmp_path)
    assert_that(result["include"]).is_equal_to(["src/**/*.ts"])
    assert_that(result["exclude"]).is_equal_to(["dist"])
    assert_that(result["files"]).is_equal_to(["globals.d.ts"])
    assert_that(result["extends"]).is_equal_to("./base.json")
    assert_that(result["composite"]).is_true()
    assert_that(result["references"]).is_length(1)
    expected_ref = str((tmp_path / "packages" / "api" / "tsconfig.json").resolve())
    assert_that(result["references"][0]).is_equal_to(expected_ref)


def test_extract_tsconfig_fields_empty_dict() -> None:
    """Return defaults for an empty dict."""
    result = extract_tsconfig_fields({}, Path("/fake"))
    assert_that(result["include"]).is_none()
    assert_that(result["exclude"]).is_none()
    assert_that(result["files"]).is_none()
    assert_that(result["references"]).is_empty()
    assert_that(result["extends"]).is_none()
    assert_that(result["composite"]).is_false()


def test_extract_tsconfig_fields_non_dict() -> None:
    """Return defaults for non-dict content."""
    result = extract_tsconfig_fields(["not", "a", "dict"], Path("/fake"))
    assert_that(result["include"]).is_none()


def test_extract_tsconfig_fields_array_extends() -> None:
    """Handle TS 5.0+ array extends syntax."""
    content = {"extends": ["./base1.json", "./base2.json"]}
    result = extract_tsconfig_fields(content, Path("/fake"))
    assert_that(result["extends"]).is_equal_to(["./base1.json", "./base2.json"])

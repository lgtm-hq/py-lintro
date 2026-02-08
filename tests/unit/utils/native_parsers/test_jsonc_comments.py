"""Tests for strip_jsonc_comments function."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.utils.jsonc import strip_jsonc_comments as _strip_jsonc_comments


def test_strip_jsonc_comments_no_comments() -> None:
    """Return unchanged content when no comments are present."""
    content = '{"key": "value", "num": 42}'
    result = _strip_jsonc_comments(content)
    assert_that(result).is_equal_to(content)


def test_strip_jsonc_comments_line_comment() -> None:
    """Strip single-line // comments from the end of lines."""
    content = '{"key": "value"} // this is a comment'
    result = _strip_jsonc_comments(content)
    assert_that(result.strip()).is_equal_to('{"key": "value"}')


def test_strip_jsonc_comments_line_comment_on_own_line() -> None:
    """Strip full-line // comments appearing at the start of lines."""
    content = '// comment at start\n{"key": "value"}'
    result = _strip_jsonc_comments(content)
    parsed = json.loads(result)
    assert_that(parsed["key"]).is_equal_to("value")


def test_strip_jsonc_comments_block_comment() -> None:
    """Strip /* */ block comments from content."""
    content = '{"key": /* comment */ "value"}'
    result = _strip_jsonc_comments(content)
    parsed = json.loads(result)
    assert_that(parsed["key"]).is_equal_to("value")


def test_strip_jsonc_comments_multiline_block_comment() -> None:
    """Strip multi-line /* */ block comments spanning multiple lines."""
    content = '{\n/* this is\na multi-line\ncomment */\n"key": "value"\n}'
    result = _strip_jsonc_comments(content)
    parsed = json.loads(result)
    assert_that(parsed["key"]).is_equal_to("value")


@pytest.mark.parametrize(
    ("content", "expected_key", "expected_value"),
    [
        (
            '{"url": "https://example.com", "pattern": "/* glob */"}',
            "url",
            "https://example.com",
        ),
        (
            '{"url": "https://example.com", "pattern": "/* glob */"}',
            "pattern",
            "/* glob */",
        ),
    ],
    ids=["preserve_url_with_slashes", "preserve_glob_pattern_in_string"],
)
def test_strip_jsonc_comments_preserve_comment_like_strings(
    content: str,
    expected_key: str,
    expected_value: str,
) -> None:
    """Preserve // and /* patterns that appear inside string values.

    Args:
        content: JSONC content with comment-like patterns in strings.
        expected_key: Key to check in the parsed result.
        expected_value: Expected value for the key.
    """
    result = _strip_jsonc_comments(content)
    parsed = json.loads(result)
    assert_that(parsed[expected_key]).is_equal_to(expected_value)


def test_strip_jsonc_comments_escape_sequence_in_string() -> None:
    """Handle escaped quotes in strings without treating them as string ends."""
    content = '{"message": "He said \\"hello\\""}'
    result = _strip_jsonc_comments(content)
    parsed = json.loads(result)
    assert_that(parsed["message"]).is_equal_to('He said "hello"')


def test_strip_jsonc_comments_backslash_in_string() -> None:
    """Handle backslash escape sequences in strings correctly."""
    content = '{"path": "C:\\\\Users\\\\test"}'
    result = _strip_jsonc_comments(content)
    parsed = json.loads(result)
    assert_that(parsed["path"]).is_equal_to("C:\\Users\\test")


def test_strip_jsonc_comments_unclosed_block_comment_warning() -> None:
    """Warn when a block comment is not properly closed."""
    content = '{"key": "value"} /* unclosed'
    with patch("lintro.utils.jsonc.logger") as mock_logger:
        _strip_jsonc_comments(content)
        mock_logger.warning.assert_called_once()


def test_strip_jsonc_comments_empty_content() -> None:
    """Handle empty content gracefully by returning empty string."""
    result = _strip_jsonc_comments("")
    assert_that(result).is_empty()


def test_strip_jsonc_comments_complex_jsonc() -> None:
    """Parse complex JSONC with multiple comment types and nested structures."""
    content = """{
  // Configuration
  "name": "test",
  /*
   * Multi-line
   * description
   */
  "settings": {
    "enabled": true // inline comment
  }
}"""
    result = _strip_jsonc_comments(content)
    parsed = json.loads(result)
    assert_that(parsed["name"]).is_equal_to("test")
    assert_that(parsed["settings"]["enabled"]).is_true()


def test_strip_jsonc_comments_unicode_content() -> None:
    """Handle Unicode characters in JSON content."""
    content = '{"message": "日本語テスト", "emoji": "🚀"}'
    result = _strip_jsonc_comments(content)
    parsed = json.loads(result)
    assert_that(parsed["message"]).is_equal_to("日本語テスト")
    assert_that(parsed["emoji"]).is_equal_to("🚀")


def test_strip_jsonc_comments_very_long_content() -> None:
    """Handle very long JSON content."""
    long_value = "x" * 10000
    content = f'{{"key": "{long_value}"}}'
    result = _strip_jsonc_comments(content)
    parsed = json.loads(result)
    assert_that(len(parsed["key"])).is_equal_to(10000)


def test_strip_jsonc_comments_nested_comments() -> None:
    """Handle nested block comment patterns (only outer is stripped)."""
    content = '{"key": "value"} /* outer /* inner */ still comment */'
    result = _strip_jsonc_comments(content)
    # The inner "/*" should be consumed by the outer comment
    assert_that('{"key": "value"}' in result).is_true()


def test_strip_jsonc_comments_consecutive_comments() -> None:
    """Handle consecutive block comments."""
    content = '/* first */ {"key": "value"} /* second */'
    result = _strip_jsonc_comments(content)
    parsed = json.loads(result)
    assert_that(parsed["key"]).is_equal_to("value")


def test_strip_jsonc_comments_mixed_quotes() -> None:
    """Handle content with different quote styles in strings."""
    content = (
        """{"single": "has 'single' quotes", "double": "has \\"double\\" quotes"}"""
    )
    result = _strip_jsonc_comments(content)
    parsed = json.loads(result)
    assert_that(parsed["single"]).contains("single")


def test_strip_jsonc_comments_only_whitespace() -> None:
    """Handle content that is only whitespace."""
    result = _strip_jsonc_comments("   \n\t  \n   ")
    assert_that(result.strip()).is_empty()


def test_strip_jsonc_comments_deeply_nested_json() -> None:
    """Handle deeply nested JSON structures."""
    content = '{"a": {"b": {"c": {"d": {"e": "deep"}}}}} // comment'
    result = _strip_jsonc_comments(content)
    parsed = json.loads(result)
    assert_that(parsed["a"]["b"]["c"]["d"]["e"]).is_equal_to("deep")

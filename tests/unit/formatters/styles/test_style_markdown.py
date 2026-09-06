"""Unit tests for MarkdownStyle formatter.

Tests verify MarkdownStyle correctly formats tabular data as valid
Markdown tables with proper escaping.
"""

from __future__ import annotations

from assertpy import assert_that

from lintro.formatters.styles.markdown import MarkdownStyle

from .conftest import MULTI_ROW_DATA, SINGLE_ROW_DATA, STANDARD_COLUMNS, TWO_COLUMNS


def test_markdown_style_single_row_produces_valid_table(
    markdown_style: MarkdownStyle,
) -> None:
    """MarkdownStyle formats single row as valid markdown table.

    Args:
        markdown_style: The MarkdownStyle formatter instance.
    """
    result = markdown_style.format(STANDARD_COLUMNS, SINGLE_ROW_DATA)
    lines = result.split("\n")

    assert_that(lines).is_length(3)  # header, separator, data row
    assert_that(result).contains("| File | Line | Message |")
    assert_that(result).contains("| --- | --- | --- |")
    assert_that(result).contains("| src/main.py | 10 | Error found |")


def test_markdown_style_multiple_rows_produces_correct_line_count(
    markdown_style: MarkdownStyle,
) -> None:
    """MarkdownStyle formats multiple rows with correct number of lines.

    Args:
        markdown_style: The MarkdownStyle formatter instance.
    """
    result = markdown_style.format(TWO_COLUMNS, MULTI_ROW_DATA)
    lines = result.split("\n")

    assert_that(lines).is_length(4)  # header, separator, 2 data rows


def test_markdown_style_row_shorter_than_columns(markdown_style: MarkdownStyle) -> None:
    """MarkdownStyle handles row with fewer elements than columns.

    Args:
        markdown_style: The MarkdownStyle formatter instance.
    """
    result = markdown_style.format(STANDARD_COLUMNS, [["src/main.py"]])

    assert_that(result).contains("src/main.py")

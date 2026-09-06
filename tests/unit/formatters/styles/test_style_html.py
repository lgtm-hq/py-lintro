"""Unit tests for HtmlStyle formatter.

Tests verify HtmlStyle correctly formats tabular data as valid HTML tables
with proper escaping for security.
"""

from __future__ import annotations

from assertpy import assert_that

from lintro.formatters.styles.html import HtmlStyle

from .conftest import MULTI_ROW_DATA, SINGLE_ROW_DATA, STANDARD_COLUMNS, TWO_COLUMNS


def test_html_style_single_row_produces_valid_table(html_style: HtmlStyle) -> None:
    """HtmlStyle formats single row as valid HTML table.

    Args:
        html_style: The HtmlStyle formatter instance.
    """
    result = html_style.format(STANDARD_COLUMNS, SINGLE_ROW_DATA)

    assert_that(result).contains("<table>")
    assert_that(result).contains("</table>")
    assert_that(result).contains("<th>File</th>")
    assert_that(result).contains("<td>src/main.py</td>")


def test_html_style_multiple_rows_produces_correct_row_count(
    html_style: HtmlStyle,
) -> None:
    """HtmlStyle formats multiple rows with correct number of table rows.

    Args:
        html_style: The HtmlStyle formatter instance.
    """
    result = html_style.format(TWO_COLUMNS, MULTI_ROW_DATA)

    assert_that(result).contains("src/a.py")
    assert_that(result).contains("src/b.py")
    assert_that(result.count("<tr>")).is_equal_to(3)  # 1 header + 2 data rows


def test_html_style_escapes_script_tags(html_style: HtmlStyle) -> None:
    """HtmlStyle escapes HTML script tags to prevent XSS.

    Args:
        html_style: The HtmlStyle formatter instance.
    """
    result = html_style.format(["Message"], [["<script>alert('XSS')</script>"]])

    assert_that(result).contains("&lt;script&gt;")
    assert_that(result).does_not_contain("<script>")


def test_html_style_row_shorter_than_columns(html_style: HtmlStyle) -> None:
    """HtmlStyle handles row with fewer elements than columns.

    Args:
        html_style: The HtmlStyle formatter instance.
    """
    result = html_style.format(STANDARD_COLUMNS, [["src/main.py"]])

    assert_that(result).contains("<td>src/main.py</td>")

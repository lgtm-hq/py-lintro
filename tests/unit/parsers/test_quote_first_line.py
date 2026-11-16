"""Quoting tool output into user-facing reasons (#3498)."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.parsers.base_parser import quote_first_line


@pytest.mark.parametrize(
    ("text", "limit", "expected"),
    [
        pytest.param(None, 20, "", id="no-output"),
        pytest.param("\n  \n", 20, "", id="blank-output"),
        pytest.param("\n  first\nsecond", 20, "first", id="first-non-empty-line"),
        pytest.param(
            "\x1b[1;31merror\x1b[0m: bad\x1b[2K\x1b[?25l",
            40,
            "error: bad",
            id="ansi-sequences",
        ),
        pytest.param("a\tb\x00c\x07d", 20, "a bcd", id="control-characters"),
        pytest.param("abcdefghij", 5, "abcd…", id="cut-to-limit"),
        pytest.param("abcde", 5, "abcde", id="exactly-at-limit"),
    ],
)
def test_quote_first_line_returns_a_clean_capped_line(
    text: str | None,
    limit: int,
    expected: str,
) -> None:
    """Only printable text from the first non-empty line survives.

    Args:
        text: Raw tool output.
        limit: Longest quote returned.
        expected: The cleaned quote.
    """
    assert_that(quote_first_line(text, limit=limit)).is_equal_to(expected)

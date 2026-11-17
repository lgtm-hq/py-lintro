"""Quoting tool output into user-facing reasons (#3498)."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.utils.quoting import quote_first_line


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
        pytest.param(
            "\x1b]0;title\x07\x1b[31mboom",
            20,
            "boom",
            id="osc-title-ended-by-bel",
        ),
        pytest.param(
            "\x1b]8;;https://x.test\x1b\\link\x1b]8;;\x1b\\ ok",
            20,
            "link ok",
            id="osc-hyperlink-ended-by-st",
        ),
        pytest.param(
            "\x1bPq#0\x1b\\a\x1b(Bb\x1b7c\x1b=d",
            20,
            "abcd",
            id="dcs-charset-and-single-escapes",
        ),
        pytest.param("\x9b31mred\x9d0;t\x9cz", 20, "redz", id="c1-csi-and-osc"),
        pytest.param(
            "\x1b]0;unterminated\nnext",
            20,
            "next",
            id="unterminated-osc-ends-at-newline",
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

"""Tests for SPDX license normalization."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.licenses.spdx import normalize_to_spdx


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("MIT", "MIT"),
        ("MIT License", "MIT"),
        ("Expat", "MIT"),
        ("Apache 2.0", "Apache-2.0"),
        ("Apache License, Version 2.0", "Apache-2.0"),
        ("Apache Software License", "Apache-2.0"),
        ("BSD", "BSD-3-Clause"),
        ("BSD-2-Clause", "BSD-2-Clause"),
        ("ISC License", "ISC"),
        ("GPL-3.0", "GPL-3.0-only"),
        ("GPLv2", "GPL-2.0-only"),
        ("AGPL-3.0-or-later", "AGPL-3.0-or-later"),
        ("LGPL-3.0", "LGPL-3.0-only"),
        ("MPL 2.0", "MPL-2.0"),
        ("PSFL", "PSF-2.0"),
        ("The Unlicense", "Unlicense"),
    ],
)
def test_normalize_known_licenses(raw: str, expected: str) -> None:
    """Known raw license strings normalize to their SPDX identifier.

    Args:
        raw: Raw license string under test.
        expected: Expected canonical SPDX identifier.
    """
    assert_that(normalize_to_spdx(raw)).is_equal_to(expected)


def test_normalize_case_insensitive() -> None:
    """Normalization ignores case and surrounding whitespace."""
    assert_that(normalize_to_spdx("  apache-2.0  ")).is_equal_to("Apache-2.0")


def test_normalize_spdx_expression_picks_first_known() -> None:
    """An OR expression resolves to the first recognized operand."""
    assert_that(normalize_to_spdx("MIT OR Apache-2.0")).is_equal_to("MIT")


def test_normalize_and_expression() -> None:
    """An AND expression prefers a restrictive/denied-class operand."""
    assert_that(normalize_to_spdx("MIT AND GPL-3.0-only")).is_equal_to("GPL-3.0-only")
    assert_that(normalize_to_spdx("GPL-3.0-only AND MIT")).is_equal_to("GPL-3.0-only")


@pytest.mark.parametrize(
    "raw",
    [None, "", "   ", "UNLICENSED", "proprietary", "totally-made-up-license"],
)
def test_normalize_unknown_returns_none(raw: str | None) -> None:
    """Empty, no-license markers, and unrecognized strings return None.

    Args:
        raw: Raw license string under test.
    """
    assert_that(normalize_to_spdx(raw)).is_none()

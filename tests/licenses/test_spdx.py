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
        ("mozilla public license 2.0 (mpl 2.0)", "MPL-2.0"),
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


def test_normalize_or_expression_preserves_operands() -> None:
    """OR expressions are preserved in normalized SPDX form."""
    assert_that(normalize_to_spdx("MIT OR Apache-2.0")).is_equal_to(
        "MIT OR Apache-2.0",
    )


def test_normalize_parenthesized_npm_expression() -> None:
    """npm-style parenthesized OR expressions normalize without outer parens."""
    assert_that(normalize_to_spdx("(MIT OR Apache-2.0)")).is_equal_to(
        "MIT OR Apache-2.0",
    )


def test_normalize_does_not_mangle_sibling_parentheticals() -> None:
    """Sibling parentheticals are not stripped as a fake outer pair."""
    assert_that(normalize_to_spdx("(MIT) OR (Apache-2.0)")).is_equal_to(
        "MIT OR Apache-2.0",
    )


def test_normalize_and_expression_preserves_operands() -> None:
    """AND expressions are preserved in normalized SPDX form."""
    assert_that(normalize_to_spdx("MIT AND GPL-3.0-only")).is_equal_to(
        "MIT AND GPL-3.0-only",
    )


def test_normalize_with_expression_preserves_exception() -> None:
    """WITH expressions keep the exception in normalized form."""
    assert_that(
        normalize_to_spdx("GPL-2.0-only WITH Classpath-exception-2.0"),
    ).is_equal_to("GPL-2.0-only WITH Classpath-exception-2.0")


def test_normalize_residual_alias_not_resolved_by_library() -> None:
    """Residual aliases still resolve spellings license-expression misses."""
    assert_that(normalize_to_spdx("Apache Software License")).is_equal_to(
        "Apache-2.0",
    )
    assert_that(normalize_to_spdx("Expat")).is_equal_to("MIT")


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

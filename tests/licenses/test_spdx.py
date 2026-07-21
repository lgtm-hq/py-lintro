"""Tests for SPDX license normalization."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.config.licenses_config import LicensesConfig
from lintro.licenses.models import LicenseStatus, PackageLicense
from lintro.licenses.policy_engine import LicensePolicyEngine
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


def test_normalize_mixed_or_and_respects_precedence() -> None:
    """AND binds tighter than OR when collapsing SPDX expressions."""
    assert_that(normalize_to_spdx("MIT OR Apache-2.0 AND GPL-3.0")).is_equal_to("MIT")
    assert_that(normalize_to_spdx("Apache-2.0 AND GPL-3.0 OR MIT")).is_equal_to(
        "GPL-3.0-only",
    )


def test_normalize_unbalanced_parentheses_rejected() -> None:
    """Unbalanced parentheses are malformed and must not normalize.

    ``_clean()`` strips outer parens, so ``(MIT OR GPL-3.0`` would otherwise be
    repaired to ``MIT OR GPL-3.0`` and false-pass by collapsing to ``MIT``.
    """
    assert_that(normalize_to_spdx("(MIT OR GPL-3.0")).is_none()
    assert_that(normalize_to_spdx("MIT OR GPL-3.0)")).is_none()
    assert_that(normalize_to_spdx("(MIT AND GPL-3.0-only")).is_none()


def test_normalize_unbalanced_parens_denied_expression_not_bypassed() -> None:
    """A malformed expression hiding a denied operand does not false-pass."""
    expression = "(GPL-3.0-only OR MIT"
    package = PackageLicense(
        name="malformed-license",
        version="1.0.0",
        license_id=normalize_to_spdx(expression),
        license_name=expression,
        ecosystem="npm",
    )
    engine = LicensePolicyEngine(LicensesConfig(policy="permissive"))
    result = engine.check(package)
    # Unrecognized/malformed license must not be silently allowed as MIT.
    assert_that(result.status).is_not_equal_to(LicenseStatus.ALLOWED)


def test_mixed_or_and_with_denied_operand_fails_policy() -> None:
    """A denied AND operand cannot be bypassed by a later OR branch."""
    expression = "Apache-2.0 AND GPL-3.0 OR MIT"
    package = PackageLicense(
        name="mixed-license",
        version="1.0.0",
        license_id=normalize_to_spdx(expression),
        license_name=expression,
        ecosystem="npm",
    )
    engine = LicensePolicyEngine(LicensesConfig(policy="permissive"))
    result = engine.check(package)
    assert_that(result.status).is_equal_to(LicenseStatus.DENIED)


def test_normalize_or_later_operand_not_split_by_hyphen() -> None:
    """Hyphenated ``-or-later`` ids must not tokenize their inner ``or``/``and``.

    A prior tokenizer matched the literal ``or`` inside ids like
    ``GPL-3.0-or-later`` (``-`` is a regex word boundary), splitting the
    operand so a copyleft conjunct collapsed to the permissive side.
    """
    assert_that(normalize_to_spdx("MIT AND GPL-3.0-or-later")).is_equal_to(
        "GPL-3.0-or-later",
    )
    assert_that(normalize_to_spdx("GPL-3.0-or-later AND MIT")).is_equal_to(
        "GPL-3.0-or-later",
    )
    assert_that(normalize_to_spdx("MIT AND LGPL-2.1-or-later")).is_equal_to(
        "LGPL-2.1-or-later",
    )
    assert_that(normalize_to_spdx("MIT AND AGPL-3.0-or-later")).is_equal_to(
        "AGPL-3.0-or-later",
    )
    assert_that(normalize_to_spdx("(MIT OR GPL-3.0-or-later)")).is_equal_to("MIT")


def test_or_later_and_operand_fails_deny_policy() -> None:
    """A copyleft ``-or-later`` AND operand cannot false-pass a deny policy."""
    expression = "MIT AND GPL-3.0-or-later"
    package = PackageLicense(
        name="copyleft-conjunct",
        version="1.0.0",
        license_id=normalize_to_spdx(expression),
        license_name=expression,
        ecosystem="npm",
    )
    engine = LicensePolicyEngine(LicensesConfig(policy="permissive"))
    result = engine.check(package)
    assert_that(result.status).is_equal_to(LicenseStatus.DENIED)


def test_or_later_or_operand_passes_policy_allowing_mit() -> None:
    """An OR expression with a copyleft ``-or-later`` branch resolves to MIT."""
    expression = "(MIT OR GPL-3.0-or-later)"
    package = PackageLicense(
        name="permissive-disjunct",
        version="1.0.0",
        license_id=normalize_to_spdx(expression),
        license_name=expression,
        ecosystem="npm",
    )
    engine = LicensePolicyEngine(LicensesConfig(policy="permissive"))
    result = engine.check(package)
    assert_that(result.status).is_equal_to(LicenseStatus.ALLOWED)


def test_or_later_denied_both_operands_fails_deny_policy() -> None:
    """When both AND operands are denied, the conjunction stays denied."""
    expression = "GPL-3.0-or-later AND AGPL-3.0-or-later"
    package = PackageLicense(
        name="copyleft-both",
        version="1.0.0",
        license_id=normalize_to_spdx(expression),
        license_name=expression,
        ecosystem="npm",
    )
    engine = LicensePolicyEngine(LicensesConfig(policy="permissive"))
    result = engine.check(package)
    assert_that(result.status).is_equal_to(LicenseStatus.DENIED)


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

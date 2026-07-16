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


def test_and_with_hyphenated_denied_operand_fails_policy() -> None:
    """An AND with a hyphenated denied operand cannot false-pass.

    The ``or`` inside ``GPL-3.0-or-later`` must not be tokenized as an SPDX
    operator; ``MIT AND GPL-3.0-or-later`` must collapse to the denied GPL
    operand so a deny policy rejects it.
    """
    expression = "MIT AND GPL-3.0-or-later"
    package = PackageLicense(
        name="hyphenated-and",
        version="1.0.0",
        license_id=normalize_to_spdx(expression),
        license_name=expression,
        ecosystem="npm",
    )
    engine = LicensePolicyEngine(
        LicensesConfig(policy="custom", denied=["GPL-3.0-or-later"]),
    )
    result = engine.check(package)
    assert_that(result.status).is_equal_to(LicenseStatus.DENIED)


def test_or_with_hyphenated_operand_passes_when_permissive_allowed() -> None:
    """An OR with a hyphenated operand resolves to the allowed branch.

    ``(MIT OR GPL-3.0-or-later)`` must tokenize on the real ``OR`` operator
    (not the ``or`` inside the hyphenated id) and select the allowed MIT
    operand so the policy passes.
    """
    expression = "(MIT OR GPL-3.0-or-later)"
    package = PackageLicense(
        name="hyphenated-or",
        version="1.0.0",
        license_id=normalize_to_spdx(expression),
        license_name=expression,
        ecosystem="npm",
    )
    engine = LicensePolicyEngine(
        LicensesConfig(policy="custom", allowed=["MIT"]),
    )
    result = engine.check(package)
    assert_that(result.status).is_equal_to(LicenseStatus.ALLOWED)


def test_or_with_hyphenated_operand_fails_when_both_denied() -> None:
    """An OR expression fails when every operand is denied by the policy.

    ``(MIT OR GPL-3.0-or-later)`` selects MIT; denying MIT alongside the
    hyphenated GPL id leaves no allowed branch, so the policy rejects it.
    """
    expression = "(MIT OR GPL-3.0-or-later)"
    package = PackageLicense(
        name="hyphenated-or-both-denied",
        version="1.0.0",
        license_id=normalize_to_spdx(expression),
        license_name=expression,
        ecosystem="npm",
    )
    engine = LicensePolicyEngine(
        LicensesConfig(
            policy="custom",
            denied=["MIT", "GPL-3.0-or-later"],
        ),
    )
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

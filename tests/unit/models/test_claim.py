"""Unit tests for the :class:`~lintro.models.core.claim.Claim` model."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.enums.capability import Cap
from lintro.models.core.claim import Claim


def test_claim_records_patterns_and_capabilities() -> None:
    """A claim keeps the patterns and capabilities it was built with."""
    claim = Claim(patterns=["*.py", "*.pyi"], capabilities={Cap.FIX, Cap.CHECK})

    assert_that(claim.patterns).is_equal_to(["*.py", "*.pyi"])
    assert_that(claim.capabilities).is_equal_to({Cap.FIX, Cap.CHECK})


def test_claim_without_capabilities_is_rejected() -> None:
    """A capability-less claim says nothing and is a declaration error."""
    with pytest.raises(ValueError, match="at least one capability"):
        Claim(patterns=["*.py"])


def test_claim_with_blank_pattern_is_rejected() -> None:
    """A blank glob would match nothing and is a declaration error."""
    with pytest.raises(ValueError, match="non-empty strings"):
        Claim(patterns=["*.py", "  "], capabilities={Cap.CHECK})


def test_project_scoped_claim_may_carry_no_patterns() -> None:
    """Tools doing their own discovery declare a pattern-less claim."""
    claim = Claim(capabilities={Cap.CHECK})

    assert_that(claim.patterns).is_empty()
    assert_that(claim.is_mutating).is_false()


@pytest.mark.parametrize(
    ("capabilities", "expected"),
    [
        ({Cap.CHECK}, False),
        ({Cap.FIX}, True),
        ({Cap.FORMAT}, True),
        ({Cap.FIX, Cap.FORMAT, Cap.CHECK}, True),
    ],
)
def test_is_mutating_reports_whether_the_claim_rewrites_files(
    capabilities: set[Cap],
    expected: bool,
) -> None:
    """``is_mutating`` is true exactly when ``FIX`` or ``FORMAT`` is held.

    Args:
        capabilities: Capabilities under test.
        expected: Whether the claim should report itself as mutating.
    """
    claim = Claim(patterns=["*.py"], capabilities=capabilities)

    assert_that(claim.is_mutating).is_equal_to(expected)


def test_mutating_capabilities_drops_check() -> None:
    """The authority rule counts mutating capabilities only."""
    claim = Claim(patterns=["*.py"], capabilities={Cap.FIX, Cap.FORMAT, Cap.CHECK})

    assert_that(claim.mutating_capabilities).is_equal_to({Cap.FIX, Cap.FORMAT})


def test_fewest_mutating_capabilities_wins_gives_format_to_black() -> None:
    """Rule (d): a dedicated formatter outranks a multi-capability tool."""
    ruff = Claim(patterns=["*.py"], capabilities={Cap.FIX, Cap.FORMAT, Cap.CHECK})
    black = Claim(patterns=["*.py"], capabilities={Cap.FORMAT, Cap.CHECK})

    assert_that(len(black.mutating_capabilities)).is_less_than(
        len(ruff.mutating_capabilities),
    )

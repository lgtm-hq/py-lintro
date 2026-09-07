"""Unit tests for the :class:`~lintro.enums.capability.Cap` enum."""

from __future__ import annotations

from assertpy import assert_that

from lintro.enums.capability import MUTATING_CAPABILITIES, Cap


def test_capability_values_are_lowercase_names() -> None:
    """``StrEnum`` + ``auto()`` yields lowercase string values."""
    assert_that([member.value for member in Cap]).is_equal_to(
        ["fix", "format", "check"],
    )


def test_capability_members_are_exactly_the_three_kinds() -> None:
    """``LINT`` and ``ANALYZE`` must never be reintroduced."""
    assert_that([member.name for member in Cap]).is_equal_to(
        ["FIX", "FORMAT", "CHECK"],
    )


def test_mutating_capabilities_excludes_check() -> None:
    """``CHECK`` never rewrites a file."""
    assert_that(Cap.CHECK in MUTATING_CAPABILITIES).is_false()
    assert_that(set(MUTATING_CAPABILITIES)).is_equal_to({Cap.FIX, Cap.FORMAT})

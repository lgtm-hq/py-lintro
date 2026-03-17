"""Tests for AI-specific enumerations."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.ai.enums import ConfidenceLevel, RiskLevel

# -- TestConfidenceLevel: Tests for ConfidenceLevel enum. --------------------


@pytest.mark.parametrize(
    ("member", "expected_value"),
    [
        (ConfidenceLevel.HIGH, "high"),
        (ConfidenceLevel.MEDIUM, "medium"),
        (ConfidenceLevel.LOW, "low"),
    ],
    ids=["high", "medium", "low"],
)
def test_confidence_string_value(
    member: ConfidenceLevel,
    expected_value: str,
) -> None:
    """ConfidenceLevel members have the expected string values."""
    assert_that(str(member)).is_equal_to(expected_value)


@pytest.mark.parametrize(
    ("member", "expected_value"),
    [
        (ConfidenceLevel.HIGH, "high"),
        (ConfidenceLevel.MEDIUM, "medium"),
        (ConfidenceLevel.LOW, "low"),
    ],
    ids=["high", "medium", "low"],
)
def test_confidence_string_equality(
    member: ConfidenceLevel,
    expected_value: str,
) -> None:
    """ConfidenceLevel members compare equal to their string values."""
    assert_that(member).is_equal_to(expected_value)
    assert_that(member == expected_value).is_true()


@pytest.mark.parametrize(
    ("member", "expected_order"),
    [
        (ConfidenceLevel.HIGH, 3),
        (ConfidenceLevel.MEDIUM, 2),
        (ConfidenceLevel.LOW, 1),
    ],
    ids=["high=3", "medium=2", "low=1"],
)
def test_confidence_numeric_order(
    member: ConfidenceLevel,
    expected_order: int,
) -> None:
    """numeric_order returns correct ordering values."""
    assert_that(member.numeric_order).is_equal_to(expected_order)


def test_ordering_high_gt_medium_gt_low() -> None:
    """HIGH > MEDIUM > LOW by numeric_order."""
    assert_that(ConfidenceLevel.HIGH.numeric_order).is_greater_than(
        ConfidenceLevel.MEDIUM.numeric_order,
    )
    assert_that(ConfidenceLevel.MEDIUM.numeric_order).is_greater_than(
        ConfidenceLevel.LOW.numeric_order,
    )


def test_confidence_is_str_subclass() -> None:
    """ConfidenceLevel members are str instances."""
    assert_that(ConfidenceLevel.HIGH).is_instance_of(str)


def test_confidence_construction_from_string() -> None:
    """ConfidenceLevel can be constructed from a string value."""
    assert_that(ConfidenceLevel("high")).is_equal_to(ConfidenceLevel.HIGH)
    assert_that(ConfidenceLevel("medium")).is_equal_to(ConfidenceLevel.MEDIUM)
    assert_that(ConfidenceLevel("low")).is_equal_to(ConfidenceLevel.LOW)


def test_confidence_invalid_value_raises() -> None:
    """Invalid string raises ValueError."""
    with pytest.raises(ValueError, match="invalid"):
        ConfidenceLevel("invalid")


# -- TestRiskLevel: Tests for RiskLevel enum. --------------------------------


@pytest.mark.parametrize(
    ("member", "expected_value"),
    [
        (RiskLevel.SAFE_STYLE, "safe-style"),
        (RiskLevel.BEHAVIORAL_RISK, "behavioral-risk"),
    ],
    ids=["safe-style", "behavioral-risk"],
)
def test_risk_hyphenated_string_value(
    member: RiskLevel,
    expected_value: str,
) -> None:
    """RiskLevel members produce hyphenated string values."""
    assert_that(str(member)).is_equal_to(expected_value)


@pytest.mark.parametrize(
    ("member", "expected_value"),
    [
        (RiskLevel.SAFE_STYLE, "safe-style"),
        (RiskLevel.BEHAVIORAL_RISK, "behavioral-risk"),
    ],
    ids=["safe-style", "behavioral-risk"],
)
def test_risk_string_equality(
    member: RiskLevel,
    expected_value: str,
) -> None:
    """RiskLevel members compare equal to their hyphenated string values."""
    assert_that(member).is_equal_to(expected_value)
    assert_that(member == expected_value).is_true()


@pytest.mark.parametrize(
    ("member", "sarif", "expected_label"),
    [
        (RiskLevel.SAFE_STYLE, False, "notice"),
        (RiskLevel.SAFE_STYLE, True, "note"),
        (RiskLevel.BEHAVIORAL_RISK, False, "warning"),
        (RiskLevel.BEHAVIORAL_RISK, True, "warning"),
    ],
    ids=[
        "safe-style-annotation",
        "safe-style-sarif",
        "behavioral-risk-annotation",
        "behavioral-risk-sarif",
    ],
)
def test_risk_to_severity_label(
    member: RiskLevel,
    sarif: bool,
    expected_label: str,
) -> None:
    """to_severity_label returns correct labels for each format."""
    assert_that(member.to_severity_label(sarif=sarif)).is_equal_to(expected_label)


def test_risk_is_str_subclass() -> None:
    """RiskLevel members are str instances."""
    assert_that(RiskLevel.SAFE_STYLE).is_instance_of(str)


def test_risk_construction_from_string() -> None:
    """RiskLevel can be constructed from a hyphenated string value."""
    assert_that(RiskLevel("safe-style")).is_equal_to(RiskLevel.SAFE_STYLE)
    assert_that(RiskLevel("behavioral-risk")).is_equal_to(
        RiskLevel.BEHAVIORAL_RISK,
    )


def test_risk_invalid_value_raises() -> None:
    """Invalid string raises ValueError."""
    with pytest.raises(ValueError, match="invalid"):
        RiskLevel("invalid")

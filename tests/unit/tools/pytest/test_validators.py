"""Tests for pytest option validation functions."""

from __future__ import annotations

import pytest

from lintro.tools.implementations.pytest.pytest_option_validators import (
    validate_pytest_options,
)

# =============================================================================
# Tests for pytest option validation functions
# =============================================================================


def test_validate_valid_options_passes() -> None:
    """Valid options pass validation without error."""
    # Should not raise - test passes if no exception
    validate_pytest_options(
        verbose=True,
        tb="short",
        maxfail=5,
        junitxml="report.xml",
    )


def test_validate_invalid_tb_raises() -> None:
    """Invalid tb value raises ValueError."""
    with pytest.raises(ValueError, match="tb must be one of"):
        validate_pytest_options(tb="invalid")


def test_validate_invalid_maxfail_raises() -> None:
    """Invalid maxfail value raises ValueError."""
    with pytest.raises(ValueError, match="maxfail must be a positive integer"):
        validate_pytest_options(maxfail=-1)


def test_validate_invalid_coverage_threshold_raises() -> None:
    """Invalid coverage_threshold raises ValueError."""
    with pytest.raises(ValueError, match="coverage_threshold must be between"):
        validate_pytest_options(coverage_threshold=101)


def test_validate_flaky_failure_rate_bounds() -> None:
    """Flaky failure rate must be between 0 and 1."""
    with pytest.raises(ValueError, match="flaky_failure_rate must be between"):
        validate_pytest_options(flaky_failure_rate=1.5)

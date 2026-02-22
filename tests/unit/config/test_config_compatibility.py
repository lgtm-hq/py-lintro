"""Unit tests for backward compatibility config functions.

This module contains function-based pytest tests for backward compatibility
functions that delegate to the unified configuration system.
"""

from __future__ import annotations

from unittest.mock import patch

from assertpy import assert_that

from lintro.utils.config import (
    get_central_line_length,
    validate_line_length_consistency,
)

# =============================================================================
# Tests for backward compatibility functions
# =============================================================================


def test_get_central_line_length_delegates_to_unified_config() -> None:
    """Verify get_central_line_length calls unified config's get_effective_line_length.

    This ensures backward compatibility is maintained while delegating to the
    new unified configuration system.
    """
    with patch(
        "lintro.utils.unified_config.get_effective_line_length",
        return_value=100,
    ) as mock_func:
        result = get_central_line_length()

    assert_that(result).is_equal_to(100)
    assert_that(result).is_instance_of(int)
    mock_func.assert_called_once()


def test_validate_line_length_consistency_delegates_to_unified_config() -> None:
    """Verify validate_line_length_consistency calls unified config's validate function.

    This ensures backward compatibility is maintained while delegating to the
    new unified configuration system.
    """
    expected_warnings = [
        "Warning 1: Inconsistent line length",
        "Warning 2: Missing config",
    ]
    with patch(
        "lintro.utils.unified_config.validate_config_consistency",
        return_value=expected_warnings,
    ) as mock_func:
        result = validate_line_length_consistency()

    assert_that(result).is_equal_to(expected_warnings)
    assert_that(result).is_length(2)
    assert_that(result).is_instance_of(list)
    mock_func.assert_called_once()


def test_validate_line_length_consistency_returns_empty_list_when_valid() -> None:
    """Verify validate_line_length_consistency returns empty list when valid."""
    with patch(
        "lintro.utils.unified_config.validate_config_consistency",
        return_value=[],
    ):
        result = validate_line_length_consistency()

    assert_that(result).is_empty()
    assert_that(result).is_instance_of(list)

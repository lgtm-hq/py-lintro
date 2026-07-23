"""Tests for PytestPlugin.set_options and fix methods."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from assertpy import assert_that

if TYPE_CHECKING:
    from lintro.tools.definitions.pytest import PytestPlugin


# =============================================================================
# Tests for PytestPlugin set_options with valid values
# =============================================================================


@pytest.mark.parametrize(
    ("option_name", "option_value"),
    [
        ("verbose", True),
        ("verbose", False),
        ("maxfail", 1),
        ("maxfail", 5),
        ("tb", "short"),
        ("tb", "long"),
        ("tb", "auto"),
        ("tb", "line"),
        ("tb", "native"),
        ("junitxml", "report.xml"),
        ("json_report", True),
        ("workers", "auto"),
        ("workers", "4"),
        ("coverage_threshold", 80.0),
        ("coverage_threshold", 0),
        ("coverage_threshold", 100),
    ],
    ids=[
        "verbose_true",
        "verbose_false",
        "maxfail_1",
        "maxfail_5",
        "tb_short",
        "tb_long",
        "tb_auto",
        "tb_line",
        "tb_native",
        "junitxml",
        "json_report",
        "workers_auto",
        "workers_4",
        "coverage_threshold_80",
        "coverage_threshold_0",
        "coverage_threshold_100",
    ],
)
def test_set_options_valid(
    sample_pytest_plugin: PytestPlugin,
    option_name: str,
    option_value: object,
) -> None:
    """Set valid options correctly.

    Args:
        sample_pytest_plugin: PytestPlugin instance for testing.
        option_name: Name of the option to set.
        option_value: Value to set for the option.
    """
    sample_pytest_plugin.set_options(**{option_name: option_value})
    assert_that(
        sample_pytest_plugin.pytest_config.get_options_dict().get(option_name),
    ).is_equal_to(
        option_value,
    )


def test_set_options_workers_int_coerces_to_string(
    sample_pytest_plugin: PytestPlugin,
) -> None:
    """Coerce integer workers to string for pytest-xdist."""
    sample_pytest_plugin.set_options(workers=4)
    assert_that(
        sample_pytest_plugin.pytest_config.get_options_dict().get("workers"),
    ).is_equal_to("4")


# =============================================================================
# Tests for PytestPlugin set_options with invalid values
# =============================================================================


@pytest.mark.parametrize(
    ("option_name", "invalid_value", "error_match"),
    [
        ("verbose", "yes", "verbose must be a boolean"),
        ("maxfail", "5", "maxfail must be a positive integer"),
        ("maxfail", -1, "maxfail must be a positive integer"),
        ("maxfail", 0, "maxfail must be a positive integer"),
        ("tb", "invalid", "tb must be one of"),
        ("junitxml", 123, "junitxml must be a string"),
        ("json_report", "yes", "json_report must be a boolean"),
        ("workers", ["4"], "workers must be a string"),
        ("coverage_threshold", "80", "coverage_threshold must be a number"),
        ("coverage_threshold", -1, "coverage_threshold must be between 0 and 100"),
        ("coverage_threshold", 101, "coverage_threshold must be between 0 and 100"),
        ("timeout", "30", "timeout must be a positive integer"),
        ("timeout", -1, "timeout must be a positive integer"),
        ("reruns", "3", "reruns must be a non-negative integer"),
        ("reruns", -1, "reruns must be a non-negative integer"),
    ],
    ids=[
        "invalid_verbose_type",
        "invalid_maxfail_string",
        "invalid_maxfail_negative",
        "invalid_maxfail_zero",
        "invalid_tb_value",
        "invalid_junitxml_type",
        "invalid_json_report_type",
        "invalid_workers_type",
        "invalid_coverage_threshold_type",
        "invalid_coverage_threshold_negative",
        "invalid_coverage_threshold_over_100",
        "invalid_timeout_string",
        "invalid_timeout_negative",
        "invalid_reruns_string",
        "invalid_reruns_negative",
    ],
)
def test_set_options_invalid_type(
    sample_pytest_plugin: PytestPlugin,
    option_name: str,
    invalid_value: object,
    error_match: str,
) -> None:
    """Raise ValueError for invalid option types.

    Args:
        sample_pytest_plugin: PytestPlugin instance for testing.
        option_name: Name of the option to set.
        invalid_value: Invalid value that should cause an error.
        error_match: Expected error message pattern.
    """
    with pytest.raises(ValueError, match=error_match):
        sample_pytest_plugin.set_options(**{option_name: invalid_value})

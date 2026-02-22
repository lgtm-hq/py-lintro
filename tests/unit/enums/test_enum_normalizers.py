"""Unit tests for enum normalizer functions."""

from __future__ import annotations

from typing import Any

import pytest
from assertpy import assert_that

from lintro.enums.bandit_levels import (
    BanditConfidenceLevel,
    BanditSeverityLevel,
    normalize_bandit_confidence_level,
    normalize_bandit_severity_level,
)
from lintro.enums.config_format import ConfigFormat, normalize_config_format
from lintro.enums.pytest_enums import (
    PytestOutputFormat,
    PytestSpecialMode,
    TestStatus,
    normalize_pytest_output_format,
    normalize_pytest_special_mode,
    normalize_test_status,
)
from lintro.enums.semgrep_enums import (
    SemgrepSeverity,
    normalize_semgrep_severity,
)
from lintro.enums.tool_type import ToolType, normalize_tool_type
from lintro.enums.tools_value import ToolsValue, normalize_tools_value

# Test cases for all normalizer functions
# Format:
# (normalize_func, enum_class, enum_member, lowercase_str, uppercase_str, error_pattern)
NORMALIZER_TEST_CASES = [
    pytest.param(
        normalize_bandit_severity_level,
        BanditSeverityLevel,
        BanditSeverityLevel.HIGH,
        "low",
        "MEDIUM",
        "Unknown bandit severity level",
        id="bandit_severity",
    ),
    pytest.param(
        normalize_bandit_confidence_level,
        BanditConfidenceLevel,
        BanditConfidenceLevel.LOW,
        "high",
        "MEDIUM",
        "Unknown bandit confidence level",
        id="bandit_confidence",
    ),
    pytest.param(
        normalize_config_format,
        ConfigFormat,
        ConfigFormat.YAML,
        "yaml",
        "JSON",
        "Unknown config format",
        id="config_format",
    ),
    pytest.param(
        normalize_pytest_special_mode,
        PytestSpecialMode,
        PytestSpecialMode.LIST_MARKERS,
        "collect_only",
        "LIST_PLUGINS",
        "Unknown pytest special mode",
        id="pytest_special_mode",
    ),
    pytest.param(
        normalize_pytest_output_format,
        PytestOutputFormat,
        PytestOutputFormat.JSON,
        "text",
        "JUNIT",
        "Unknown pytest output format",
        id="pytest_output_format",
    ),
    pytest.param(
        normalize_test_status,
        TestStatus,
        TestStatus.PASSED,
        "failed",
        "SKIPPED",
        "Unknown test status",
        id="test_status",
    ),
    pytest.param(
        normalize_tools_value,
        ToolsValue,
        ToolsValue.ALL,
        "all",
        "ALL",
        "Unknown tools value",
        id="tools_value",
    ),
    pytest.param(
        normalize_semgrep_severity,
        SemgrepSeverity,
        SemgrepSeverity.WARNING,
        "info",
        "ERROR",
        "Invalid Semgrep severity",
        id="semgrep_severity",
    ),
]


@pytest.mark.parametrize(
    "normalize_func,enum_class,enum_member,lowercase_str,uppercase_str,error_pattern",
    NORMALIZER_TEST_CASES,
)
def test_normalizer_with_enum_instance(
    normalize_func: Any,
    enum_class: Any,
    enum_member: Any,
    lowercase_str: str,
    uppercase_str: str,
    error_pattern: str,
) -> None:
    """Return enum instance unchanged.

    Args:
        normalize_func: The normalizer function to test.
        enum_class: The enum class being tested.
        enum_member: An instance of the enum class.
        lowercase_str: A lowercase string representation.
        uppercase_str: An uppercase string representation.
        error_pattern: The error pattern for invalid inputs.
    """
    result = normalize_func(enum_member)
    assert_that(result).is_equal_to(enum_member)


@pytest.mark.parametrize(
    "normalize_func,enum_class,enum_member,lowercase_str,uppercase_str,error_pattern",
    NORMALIZER_TEST_CASES,
)
def test_normalizer_with_lowercase_string(
    normalize_func: Any,
    enum_class: Any,
    enum_member: Any,
    lowercase_str: str,
    uppercase_str: str,
    error_pattern: str,
) -> None:
    """Normalize lowercase string to enum.

    Args:
        normalize_func: The normalizer function to test.
        enum_class: The enum class being tested.
        enum_member: An instance of the enum class.
        lowercase_str: A lowercase string representation.
        uppercase_str: An uppercase string representation.
        error_pattern: The error pattern for invalid inputs.
    """
    result = normalize_func(lowercase_str)
    assert_that(result).is_instance_of(enum_class)


@pytest.mark.parametrize(
    "normalize_func,enum_class,enum_member,lowercase_str,uppercase_str,error_pattern",
    NORMALIZER_TEST_CASES,
)
def test_normalizer_with_uppercase_string(
    normalize_func: Any,
    enum_class: Any,
    enum_member: Any,
    lowercase_str: str,
    uppercase_str: str,
    error_pattern: str,
) -> None:
    """Normalize uppercase string to enum.

    Args:
        normalize_func: The normalizer function to test.
        enum_class: The enum class being tested.
        enum_member: An instance of the enum class.
        lowercase_str: A lowercase string representation.
        uppercase_str: An uppercase string representation.
        error_pattern: The error pattern for invalid inputs.
    """
    result = normalize_func(uppercase_str)
    assert_that(result).is_instance_of(enum_class)


@pytest.mark.parametrize(
    "normalize_func,enum_class,enum_member,lowercase_str,uppercase_str,error_pattern",
    NORMALIZER_TEST_CASES,
)
def test_normalizer_with_invalid_string(
    normalize_func: Any,
    enum_class: Any,
    enum_member: Any,
    lowercase_str: str,
    uppercase_str: str,
    error_pattern: str,
) -> None:
    """Raise ValueError for invalid string.

    Args:
        normalize_func: The normalizer function to test.
        enum_class: The enum class being tested.
        enum_member: An instance of the enum class.
        lowercase_str: A lowercase string representation.
        uppercase_str: An uppercase string representation.
        error_pattern: The error pattern for invalid inputs.
    """
    with pytest.raises(ValueError, match=error_pattern):
        normalize_func("definitely_invalid_xyz_12345")


# --- ToolType-specific tests (has additional behavior) ---


def test_normalize_tool_type_with_combined_enum() -> None:
    """Preserve combined Flag values for ToolType."""
    combined = ToolType.LINTER | ToolType.FORMATTER
    result = normalize_tool_type(combined)
    assert_that(result).is_equal_to(combined)


def test_normalize_tool_type_with_invalid_type() -> None:
    """Raise ValueError for invalid type."""
    with pytest.raises(ValueError, match="Invalid tool type"):
        normalize_tool_type(123)  # type: ignore

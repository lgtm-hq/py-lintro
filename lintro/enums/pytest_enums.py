"""Pytest-related enum definitions.

This module defines enums for pytest special modes, output formats, and test statuses.
"""

from __future__ import annotations

from enum import StrEnum, auto


class TestStatus(StrEnum):
    """Supported test status values.

    Values are lower-case string identifiers (auto-generated from member names).
    """

    __test__ = False

    PASSED = auto()
    FAILED = auto()
    ERROR = auto()
    SKIPPED = auto()
    UNKNOWN = auto()


class PytestSpecialMode(StrEnum):
    """Supported special modes for pytest execution.

    Values are lower-case string identifiers to align with pytest command-line options.
    """

    LIST_PLUGINS = auto()
    CHECK_PLUGINS = auto()
    COLLECT_ONLY = auto()
    LIST_FIXTURES = auto()
    FIXTURE_INFO = auto()
    LIST_MARKERS = auto()
    PARAMETRIZE_HELP = auto()


class PytestOutputFormat(StrEnum):
    """Supported output formats for pytest results.

    Values are lower-case string identifiers to align with pytest options.
    """

    JSON = auto()
    JUNIT = auto()
    TEXT = auto()


class PytestParallelPreset(StrEnum):
    """Supported parallel execution presets for pytest-xdist.

    Values are lower-case string identifiers for preset names.
    """

    AUTO = auto()
    SMALL = auto()
    MEDIUM = auto()
    LARGE = auto()


def normalize_pytest_special_mode(value: str | PytestSpecialMode) -> PytestSpecialMode:
    """Normalize a raw value to a PytestSpecialMode enum.

    Args:
        value: str or PytestSpecialMode to normalize.

    Returns:
        PytestSpecialMode: Normalized enum value.

    Raises:
        ValueError: If value is not a valid special mode.
    """
    if isinstance(value, PytestSpecialMode):
        return value
    try:
        return PytestSpecialMode[value.upper()]
    except KeyError as err:
        raise ValueError(
            f"Unknown pytest special mode: {value!r}. "
            f"Supported modes: {list(PytestSpecialMode)}",
        ) from err


def normalize_pytest_output_format(
    value: str | PytestOutputFormat,
) -> PytestOutputFormat:
    """Normalize a raw value to a PytestOutputFormat enum.

    Args:
        value: str or PytestOutputFormat to normalize.

    Returns:
        PytestOutputFormat: Normalized enum value.

    Raises:
        ValueError: If value is not a valid output format.
    """
    if isinstance(value, PytestOutputFormat):
        return value
    try:
        return PytestOutputFormat[value.upper()]
    except KeyError as err:
        raise ValueError(
            f"Unknown pytest output format: {value!r}. "
            f"Supported formats: {list(PytestOutputFormat)}",
        ) from err


def normalize_test_status(value: str | TestStatus) -> TestStatus:
    """Normalize a raw value to a TestStatus enum.

    Args:
        value: str or TestStatus to normalize.

    Returns:
        TestStatus: Normalized enum value.

    Raises:
        ValueError: If value is not a valid test status.
    """
    if isinstance(value, TestStatus):
        return value
    try:
        return TestStatus[value.upper()]
    except KeyError as err:
        supported = f"Supported statuses: {list(TestStatus)}"
        raise ValueError(
            f"Unknown test status: {value!r}. {supported}",
        ) from err

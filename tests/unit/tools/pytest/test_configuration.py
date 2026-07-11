"""Tests for PytestConfiguration class."""

from __future__ import annotations

from assertpy import assert_that

from lintro.enums.pytest_enums import PytestSpecialMode
from lintro.tools.implementations.pytest.pytest_config import PytestConfiguration

# =============================================================================
# Tests for PytestConfiguration class
# =============================================================================


def test_default_values(sample_pytest_config: PytestConfiguration) -> None:
    """Configuration has correct default values.

    Args:
        sample_pytest_config: The PytestConfiguration instance to test.
    """
    assert_that(sample_pytest_config.verbose).is_none()
    assert_that(sample_pytest_config.tb).is_none()
    assert_that(sample_pytest_config.maxfail).is_none()


def test_set_options(sample_pytest_config: PytestConfiguration) -> None:
    """Configuration set_options works correctly.

    Args:
        sample_pytest_config: The PytestConfiguration instance to test.
    """
    sample_pytest_config.set_options(verbose=True, tb="short", maxfail=5)
    assert_that(sample_pytest_config.verbose).is_true()
    assert_that(sample_pytest_config.tb).is_equal_to("short")
    assert_that(sample_pytest_config.maxfail).is_equal_to(5)


def test_get_options_dict(sample_pytest_config: PytestConfiguration) -> None:
    """Configuration get_options_dict returns only non-None values.

    Args:
        sample_pytest_config: The PytestConfiguration instance to test.
    """
    sample_pytest_config.set_options(verbose=True, tb="short")
    options = sample_pytest_config.get_options_dict()
    assert_that(options).contains_key("verbose")
    assert_that(options).contains_key("tb")
    assert_that(options.get("verbose")).is_true()
    assert_that(options.get("tb")).is_equal_to("short")


def test_is_special_mode_false_by_default(
    sample_pytest_config: PytestConfiguration,
) -> None:
    """Configuration is_special_mode returns False by default.

    Args:
        sample_pytest_config: The PytestConfiguration instance to test.
    """
    assert_that(sample_pytest_config.is_special_mode()).is_false()


def test_is_special_mode_true_when_set(
    sample_pytest_config: PytestConfiguration,
) -> None:
    """Configuration is_special_mode returns True when special mode is set.

    Args:
        sample_pytest_config: The PytestConfiguration instance to test.
    """
    sample_pytest_config.set_options(collect_only=True)
    assert_that(sample_pytest_config.is_special_mode()).is_true()


def test_get_special_mode_none_by_default(
    sample_pytest_config: PytestConfiguration,
) -> None:
    """Configuration get_special_mode returns None by default.

    Args:
        sample_pytest_config: The PytestConfiguration instance to test.
    """
    assert_that(sample_pytest_config.get_special_mode()).is_none()


def test_get_special_mode_returns_correct_mode(
    sample_pytest_config: PytestConfiguration,
) -> None:
    """Configuration get_special_mode returns the correct mode name.

    Args:
        sample_pytest_config: The PytestConfiguration instance to test.
    """
    sample_pytest_config.set_options(list_plugins=True)
    assert_that(sample_pytest_config.get_special_mode()).is_equal_to(
        PytestSpecialMode.LIST_PLUGINS.value,
    )

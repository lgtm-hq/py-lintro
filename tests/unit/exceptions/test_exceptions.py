"""Unit tests for custom exception hierarchy and messages."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.exceptions.errors import (
    ConfigurationError,
    FileAccessError,
    InvalidToolConfigError,
    InvalidToolOptionError,
    LintroError,
    ParserError,
    ToolExecutionError,
    ToolTimeoutError,
)


@pytest.mark.parametrize(
    "exception_class,message",
    [
        (LintroError, "base error"),
        (InvalidToolConfigError, "invalid config"),
        (InvalidToolOptionError, "invalid option"),
        (ToolExecutionError, "execution failed"),
        (ToolTimeoutError, "timeout occurred"),
        (ParserError, "parsing failed"),
        (ConfigurationError, "config error"),
        (FileAccessError, "file not found"),
    ],
    ids=[
        "LintroError",
        "InvalidToolConfigError",
        "InvalidToolOptionError",
        "ToolExecutionError",
        "ToolTimeoutError",
        "ParserError",
        "ConfigurationError",
        "FileAccessError",
    ],
)
def test_exception_inheritance_and_message(exception_class: type, message: str) -> None:
    """Test all exceptions inherit from LintroError and preserve messages.

    Args:
        exception_class: Exception class to test.
        message: Error message to use.
    """
    exc = exception_class(message)
    assert_that(exc).is_instance_of(LintroError)
    assert_that(exc).is_instance_of(Exception)
    assert_that(str(exc)).is_equal_to(message)


@pytest.mark.parametrize(
    "exception_class",
    [
        LintroError,
        InvalidToolConfigError,
        InvalidToolOptionError,
        ToolExecutionError,
        ToolTimeoutError,
        ParserError,
        ConfigurationError,
        FileAccessError,
    ],
)
def test_exception_can_be_raised_and_caught(exception_class: type) -> None:
    """Test exceptions can be raised and caught properly.

    Args:
        exception_class: Exception class to test.

    Raises:
        exception_class: The exception being tested.
    """
    with pytest.raises(exception_class) as exc_info:
        raise exception_class("test message")
    assert_that(str(exc_info.value)).is_equal_to("test message")


@pytest.mark.parametrize(
    "exception_class",
    [
        InvalidToolConfigError,
        InvalidToolOptionError,
        ToolExecutionError,
        ToolTimeoutError,
        ParserError,
        ConfigurationError,
        FileAccessError,
    ],
)
def test_subclass_caught_by_base_exception(exception_class: type) -> None:
    """Test subclass exceptions can be caught by LintroError.

    Args:
        exception_class: Exception class to test.

    Raises:
        exception_class: The exception being tested.
    """
    with pytest.raises(LintroError):
        raise exception_class("caught by base")


def test_configuration_error_is_value_error() -> None:
    """ConfigurationError must remain catchable as ValueError."""
    exc = ConfigurationError("bad config")

    assert_that(exc).is_instance_of(ValueError)
    assert_that(exc).is_instance_of(LintroError)


def test_exception_args_preserved() -> None:
    """Test exception args are preserved for introspection."""
    exc = ToolExecutionError("tool failed", "extra", "info")
    assert_that(exc.args).is_equal_to(("tool failed", "extra", "info"))


def test_exception_chaining() -> None:
    """Test exceptions can be chained with __cause__."""
    original = ValueError("original error")
    try:
        try:
            raise original
        except ValueError as e:
            raise ParserError("parsing failed") from e
    except ParserError as pe:
        assert_that(pe.__cause__).is_equal_to(original)
        assert_that(str(pe.__cause__)).is_equal_to("original error")

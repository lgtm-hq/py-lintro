"""Unit tests for version_checking module."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.tools.core.version_checking import (
    _get_version_timeout,
    get_install_hints,
    get_minimum_versions,
)

# Tests for _get_version_timeout


def test_get_version_timeout_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Return default timeout when env var not set.

    Args:
        monkeypatch: Pytest fixture for patching modules and attributes.
    """
    monkeypatch.delenv("LINTRO_VERSION_TIMEOUT", raising=False)
    result = _get_version_timeout()
    assert_that(result).is_equal_to(30)


def test_get_version_timeout_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Return parsed timeout from env var.

    Args:
        monkeypatch: Pytest fixture for patching modules and attributes.
    """
    monkeypatch.setenv("LINTRO_VERSION_TIMEOUT", "60")
    result = _get_version_timeout()
    assert_that(result).is_equal_to(60)


@pytest.mark.parametrize(
    ("env_value", "expected"),
    [
        ("invalid", 30),
        ("-5", 30),
        ("0", 30),
    ],
    ids=["non_numeric", "negative", "zero"],
)
def test_get_version_timeout_invalid(
    env_value: str,
    expected: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return default on invalid timeout values.

    Args:
        env_value: The environment variable value to test.
        expected: The expected timeout value.
        monkeypatch: Pytest monkeypatch fixture for environment manipulation.
    """
    monkeypatch.setenv("LINTRO_VERSION_TIMEOUT", env_value)
    result = _get_version_timeout()
    assert_that(result).is_equal_to(expected)


# Tests for get_minimum_versions


def test_get_minimum_versions_returns_dict() -> None:
    """Return a dictionary of tool versions."""
    result = get_minimum_versions()
    assert_that(result).is_instance_of(dict)
    assert_that(result).is_not_empty()


def test_get_minimum_versions_contains_expected_tools() -> None:
    """Return versions for expected external tools."""
    result = get_minimum_versions()
    # Check for some expected tools
    expected_tools = ["hadolint", "actionlint"]
    for tool in expected_tools:
        assert_that(tool in result).is_true()


def test_get_minimum_versions_returns_copy() -> None:
    """Return a copy, not the original dict."""
    result1 = get_minimum_versions()
    result2 = get_minimum_versions()
    # Should be equal but not the same object
    assert_that(result1).is_equal_to(result2)
    # Modifying one shouldn't affect the other
    result1["test_tool"] = "1.0.0"
    assert_that("test_tool" in result2).is_false()


# Tests for get_install_hints


def test_get_install_hints_returns_dict() -> None:
    """Return a dictionary of install hints."""
    result = get_install_hints()
    assert_that(result).is_instance_of(dict)
    assert_that(result).is_not_empty()


def test_get_install_hints_pip_for_python_tools() -> None:
    """Python tools have pip/uv install hints."""
    result = get_install_hints()
    # pytest is a Python tool that should have pip/uv hints
    assert_that("pip install" in result.get("pytest", "")).is_true()
    assert_that("uv add" in result.get("pytest", "")).is_true()


def test_get_install_hints_bun_for_node_tools() -> None:
    """Node.js tools have bun install hints."""
    result = get_install_hints()
    assert_that("bun add" in result.get("markdownlint", "")).is_true()


def test_get_install_hints_contains_commitlint_package_alias() -> None:
    """Package-keyed commitlint versions have install hints."""
    result = get_install_hints()

    assert_that(result).contains_key("@commitlint/cli")
    assert_that(result["@commitlint/cli"]).contains("@commitlint/cli@")


def test_get_install_hints_external_tools() -> None:
    """External tools have appropriate install hints."""
    result = get_install_hints()
    assert_that("github" in result.get("hadolint", "").lower()).is_true()
    assert_that("rustup" in result.get("clippy", "")).is_true()

"""Unit tests for version_checking module."""

from __future__ import annotations

import pytest
from assertpy import assert_that
from loguru import logger

from lintro.tools.core import version_checking
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


def test_get_install_hints_includes_commitlint_cli_alias() -> None:
    """``@commitlint/cli`` npm alias has the same bun install hint as commitlint."""
    result = get_install_hints()
    assert_that(result).contains_key("@commitlint/cli")
    assert_that(result["@commitlint/cli"]).contains("bun add")
    assert_that(result["@commitlint/cli"]).contains("@commitlint/cli@")
    assert_that(result["@commitlint/cli"]).is_equal_to(result["commitlint"])


def test_get_install_hints_includes_spectral_cli_alias() -> None:
    """``@stoplight/spectral-cli`` npm alias has the same bun install hint.

    The hint must be a plain argv-safe string: no nested quotes or backticks
    around the scoped package name.
    """
    result = get_install_hints()
    hint = result["spectral"]
    assert_that(result).contains_key("@stoplight/spectral-cli")
    assert_that(hint).contains("bun add")
    assert_that(hint).contains("@stoplight/spectral-cli@")
    assert_that(hint).does_not_contain("`")
    assert_that(result["@stoplight/spectral-cli"]).is_equal_to(hint)


def test_get_install_hints_uses_commitlint_companion_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Commitlint hint uses each npm package's resolved version."""
    monkeypatch.setattr(
        version_checking,
        "get_minimum_versions",
        lambda: {"commitlint": "21.2.1", "@commitlint/cli": "21.2.1"},
    )
    monkeypatch.setattr(
        version_checking,
        "get_tool_version",
        lambda package: (
            "21.2.0" if package == "@commitlint/config-conventional" else None
        ),
    )

    result = get_install_hints()

    assert_that(result["commitlint"]).contains("@commitlint/cli@21.2.1")
    assert_that(result["commitlint"]).contains(
        "@commitlint/config-conventional@21.2.0",
    )
    assert_that(result["@commitlint/cli"]).is_equal_to(result["commitlint"])


def test_get_install_hints_falls_back_when_companion_version_unresolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Commitlint hints survive an unresolvable companion version (#1663).

    Args:
        monkeypatch: Pytest fixture for patching modules and attributes.
    """
    monkeypatch.setattr(
        version_checking,
        "get_minimum_versions",
        lambda: {"commitlint": "21.2.1", "@commitlint/cli": "21.2.1"},
    )
    monkeypatch.setattr(version_checking, "get_tool_version", lambda _package: None)

    result = get_install_hints()

    assert_that(result).contains_key("commitlint")
    assert_that(result).contains_key("@commitlint/cli")
    for key in ("commitlint", "@commitlint/cli"):
        assert_that(result[key]).does_not_contain("{")
        assert_that(result[key]).contains("@commitlint/cli@21.2.1")
        assert_that(result[key]).contains("@commitlint/config-conventional@21.2.1")


def test_get_install_hints_missing_template_logs_debug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing hint templates log at debug, not warning (#1593 / #1425).

    Args:
        monkeypatch: Pytest fixture for patching modules and attributes.
    """
    monkeypatch.setattr(
        version_checking,
        "get_minimum_versions",
        lambda: {"hadolint": "2.12.0", "totally_missing_tool": "1.0.0"},
    )
    version_checking._logged_warnings.clear()
    records: list[tuple[str, str]] = []

    def sink(message: object) -> None:
        """Collect the level and text of each loguru record.

        Args:
            message: Loguru message object carrying the record.
        """
        record = message.record  # type: ignore[attr-defined]
        records.append((record["level"].name, record["message"]))

    sink_id = logger.add(sink, level="DEBUG", format="{message}")
    try:
        hints = get_install_hints()
    finally:
        logger.remove(sink_id)

    debug_messages = [text for level, text in records if level == "DEBUG"]
    assert_that([text for level, text in records if level == "WARNING"]).is_empty()
    assert_that("\n".join(debug_messages)).contains("Missing install hints")
    assert_that("\n".join(debug_messages)).contains("totally_missing_tool")
    assert_that(hints).does_not_contain_key("totally_missing_tool")


def test_get_install_hints_external_tools() -> None:
    """External tools have appropriate install hints."""
    result = get_install_hints()
    assert_that("github" in result.get("hadolint", "").lower()).is_true()
    assert_that("rustup" in result.get("clippy", "")).is_true()


def test_get_install_hints_semgrep_is_isolated() -> None:
    """Doctor must not suggest installing semgrep via pip or lintro extras."""
    result = get_install_hints()
    hint = result.get("semgrep", "")
    assert_that(hint).contains("install-semgrep.sh")
    assert_that(hint).does_not_contain("pip install")
    assert_that(hint).does_not_contain("lintro[tools]")

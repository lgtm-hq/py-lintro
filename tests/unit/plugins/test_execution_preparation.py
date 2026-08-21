"""Unit tests for execution_preparation version-lag helpers."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.plugins.execution_preparation import (  # noqa: SLF001
    _parse_allow_version_lag,
    _version_lag_allowed,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, set()),
        ("", set()),
        ("  ", set()),
        ("trufflehog", {"trufflehog"}),
        ("trufflehog,oxlint", {"trufflehog", "oxlint"}),
        (" TruffleHog , OxLint ", {"trufflehog", "oxlint"}),
        ("*", None),
    ],
    ids=[
        "unset",
        "empty",
        "blank",
        "single",
        "multi",
        "case-and-spaces",
        "star",
    ],
)
def test_parse_allow_version_lag(raw: str | None, expected: set[str] | None) -> None:
    """Parse LINTRO_ALLOW_VERSION_LAG into a name set or star sentinel.

    Args:
        raw: Raw env string under test.
        expected: Expected parsed value.
    """
    assert_that(_parse_allow_version_lag(raw)).is_equal_to(expected)


def test_version_lag_allowed_for_listed_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    """Listed tools are allowed; others are not.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setenv("LINTRO_ALLOW_VERSION_LAG", "trufflehog,oxlint")
    assert_that(_version_lag_allowed("trufflehog")).is_true()
    assert_that(_version_lag_allowed("TRUFFLEHOG")).is_true()
    assert_that(_version_lag_allowed("ruff")).is_false()


def test_version_lag_allowed_star(monkeypatch: pytest.MonkeyPatch) -> None:
    """Star allowlists every tool.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setenv("LINTRO_ALLOW_VERSION_LAG", "*")
    assert_that(_version_lag_allowed("anything")).is_true()


def test_version_lag_disallowed_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset env keeps full min-version enforcement.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.delenv("LINTRO_ALLOW_VERSION_LAG", raising=False)
    assert_that(_version_lag_allowed("trufflehog")).is_false()


def test_verify_tool_version_uses_cached_snapshot() -> None:
    """Version gates read ToolSnapshot flags, not a live subprocess probe.

    cwd-scoped binary resolution is reserved for #1727; snapshots currently
    resolve via ``get_tool_snapshot`` without an execution cwd.
    """
    from unittest.mock import patch

    from lintro.plugins.execution_preparation import verify_tool_version
    from lintro.tools.core.snapshots import ToolCapabilities, ToolSnapshot

    definition = type("Def", (), {"name": "html_validate"})()
    snap = ToolSnapshot(
        name="html_validate",
        available=True,
        version="1.0.0",
        capabilities=ToolCapabilities(),
        version_check_passed=True,
        min_version="1.0.0",
    )

    with patch(
        "lintro.tools.core.snapshots.get_tool_snapshot",
        return_value=snap,
    ) as mock_snap:
        result = verify_tool_version(definition, cwd="/tmp/target-project")

    assert_that(result).is_none()
    mock_snap.assert_called_once_with("html_validate")

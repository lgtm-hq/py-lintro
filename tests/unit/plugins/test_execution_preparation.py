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


def test_verify_tool_version_resolves_from_the_execution_cwd() -> None:
    """The version check must resolve the binary the run will actually use.

    Verification previously resolved from the process working directory while
    execution resolved from the target project. A missing or outdated
    process-visible binary could then skip the run even though the target had
    a valid lockfile-pinned install — and the reverse, passing on a binary
    that never executes (#1727).

    """
    from unittest.mock import patch

    from lintro.plugins.execution_preparation import verify_tool_version

    seen: dict[str, object] = {}

    def _capture(tool_name: str, cwd: object = None) -> list[str]:
        seen["cwd"] = cwd
        return ["html-validate"]

    definition = type("Def", (), {"name": "html_validate"})()

    with (
        patch(
            "lintro.plugins.execution_preparation.get_executable_command",
            side_effect=_capture,
        ),
        patch(
            "lintro.tools.core.version_requirements.check_tool_version",
        ) as mock_check,
    ):
        mock_check.return_value = type(
            "Info",
            (),
            {
                "version_check_passed": True,
                "below_recommended": False,
                "current_version": "1.0.0",
                "recommended_version": "1.0.0",
                "min_version": "1.0.0",
            },
        )()
        verify_tool_version(definition, cwd="/tmp/target-project")

    assert_that(seen.get("cwd")).is_equal_to("/tmp/target-project")

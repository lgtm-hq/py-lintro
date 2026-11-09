"""Allowlist behaviour of ``get_tools_to_run`` for ``enabled_tools`` (#3066)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.config.config_loader import clear_config_cache
from lintro.config.execution_config import ExecutionConfig
from lintro.config.lintro_config import LintroConfig, LintroToolConfig
from lintro.tools import tool_manager
from lintro.utils.execution.tool_configuration import get_tools_to_run


@pytest.fixture(autouse=True)
def _registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset the config cache and fake a small check registry.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    clear_config_cache()
    monkeypatch.setattr(
        tool_manager,
        "get_check_tools",
        lambda: ["ruff", "mypy", "bandit"],
    )
    monkeypatch.setattr(
        tool_manager,
        "is_tool_registered",
        lambda name: name in ["ruff", "mypy", "bandit"],
    )
    monkeypatch.setattr(
        tool_manager,
        "get_tool_names",
        lambda: ["ruff", "mypy", "bandit"],
    )


def _run(config: LintroConfig, tools: str | None) -> object:
    """Run ``get_tools_to_run`` against a fixed config.

    Args:
        config: Config returned by the patched loader.
        tools: ``--tools`` value.

    Returns:
        The ``ToolsToRunResult``.
    """
    with patch(
        "lintro.utils.execution.tool_configuration.get_config",
        return_value=config,
    ):
        return get_tools_to_run(tools=tools, action="check")


@pytest.mark.parametrize("tools", [None, "all"], ids=["default", "all"])
def test_outsiders_are_counted_not_listed(tools: str | None) -> None:
    """Tools outside a non-empty allowlist never become skip rows.

    Args:
        tools: ``--tools`` value (default or ``all``).
    """
    config = LintroConfig(execution=ExecutionConfig(enabled_tools=["ruff"]))

    result = _run(config, tools)

    assert_that(result.to_run).is_equal_to(["ruff"])  # type: ignore[attr-defined]
    assert_that(result.skipped).is_empty()  # type: ignore[attr-defined]
    assert_that(result.not_in_allowlist).is_equal_to(  # type: ignore[attr-defined]
        ["mypy", "bandit"],
    )


def test_absent_allowlist_leaves_everything_running() -> None:
    """Absent ``enabled_tools`` keeps the all-tools behaviour."""
    result = _run(LintroConfig(), None)

    assert_that(sorted(result.to_run)).is_equal_to(  # type: ignore[attr-defined]
        ["bandit", "mypy", "ruff"],
    )
    assert_that(result.not_in_allowlist).is_empty()  # type: ignore[attr-defined]


def test_disabled_in_config_row_survives_inside_allowlist() -> None:
    """``tools.<name>.enabled: false`` inside the allowlist still shows a row."""
    config = LintroConfig(
        execution=ExecutionConfig(enabled_tools=["ruff", "mypy"]),
        tools={"mypy": LintroToolConfig(enabled=False)},
    )

    result = _run(config, None)

    assert_that(result.to_run).is_equal_to(["ruff"])  # type: ignore[attr-defined]
    assert_that(
        [(s.name, s.reason) for s in result.skipped],  # type: ignore[attr-defined]
    ).is_equal_to([("mypy", "disabled in config")])
    assert_that(result.not_in_allowlist).is_equal_to(["bandit"])  # type: ignore[attr-defined]


def test_explicit_tools_still_bypass_allowlist() -> None:
    """Named ``--tools`` ignore the allowlist (#1415) and report nothing hidden."""
    config = LintroConfig(execution=ExecutionConfig(enabled_tools=["ruff"]))

    result = _run(config, "mypy")

    assert_that(result.to_run).is_equal_to(["mypy"])  # type: ignore[attr-defined]
    assert_that(result.skipped).is_empty()  # type: ignore[attr-defined]
    assert_that(result.not_in_allowlist).is_empty()  # type: ignore[attr-defined]

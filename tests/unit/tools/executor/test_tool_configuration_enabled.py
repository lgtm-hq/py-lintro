"""Tests for tool enabled/disabled functionality in get_tools_to_run."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.config.config_loader import clear_config_cache
from lintro.config.execution_config import ExecutionConfig
from lintro.config.lintro_config import LintroConfig, LintroToolConfig
from lintro.utils.execution.tool_configuration import get_tools_to_run

# =============================================================================
# Fixtures
# =============================================================================


class _FakeToolDefinition:
    """Fake ToolDefinition for testing."""

    def __init__(self, name: str, can_fix: bool = True) -> None:
        self.name = name
        self.can_fix = can_fix
        self.description = ""
        self.file_patterns: list[str] = []
        self.native_configs: list[str] = []
        self.conflicts_with: list[str] = []


class _FakeTool:
    """Fake tool for testing."""

    def __init__(self, name: str, can_fix: bool = True) -> None:
        self._definition = _FakeToolDefinition(name=name, can_fix=can_fix)

    @property
    def definition(self) -> _FakeToolDefinition:
        return self._definition

    def set_options(self, **kwargs: Any) -> None:
        pass


@pytest.fixture(autouse=True)
def _reset_config_cache() -> None:
    """Reset config cache before each test."""
    clear_config_cache()


# =============================================================================
# Disabled tools - check action
# =============================================================================


def test_disabled_tool_skipped_in_all_tools_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Disabled tool should be skipped when using 'all' for check action."""
    from lintro.tools import tool_manager

    monkeypatch.setattr(
        tool_manager,
        "get_check_tools",
        lambda: ["ruff", "mypy", "bandit"],
    )

    config = LintroConfig(
        tools={"mypy": LintroToolConfig(enabled=False)},
    )

    with patch(
        "lintro.utils.execution.tool_configuration.get_config",
        return_value=config,
    ):
        result = get_tools_to_run(tools=None, action="check")

    assert_that(result.to_run).contains("ruff", "bandit")
    assert_that(result.to_run).does_not_contain("mypy")
    # Verify mypy appears in skipped list with reason
    skipped_by_name = {s.name: s for s in result.skipped}
    assert_that(skipped_by_name).contains_key("mypy")
    assert_that(skipped_by_name["mypy"].reason).is_equal_to("disabled in config")


def test_disabled_tool_skipped_in_all_tools_fix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Disabled tool should be skipped when using 'all' for fix action."""
    from lintro.tools import tool_manager

    monkeypatch.setattr(
        tool_manager,
        "get_fix_tools",
        lambda: ["ruff", "black", "prettier"],
    )

    config = LintroConfig(
        tools={"black": LintroToolConfig(enabled=False)},
    )

    with patch(
        "lintro.utils.execution.tool_configuration.get_config",
        return_value=config,
    ):
        result = get_tools_to_run(tools="all", action="fmt")

    assert_that(result.to_run).contains("ruff", "prettier")
    assert_that(result.to_run).does_not_contain("black")
    skipped_names = [s.name for s in result.skipped]
    assert_that(skipped_names).contains("black")


# =============================================================================
# Disabled tools - explicit selection
# =============================================================================


def test_disabled_tool_skipped_when_explicitly_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Disabled tool should be skipped even when explicitly requested."""
    from lintro.tools import tool_manager

    monkeypatch.setattr(
        tool_manager,
        "is_tool_registered",
        lambda name: name in ["ruff", "mypy"],
    )
    monkeypatch.setattr(
        tool_manager,
        "get_tool_names",
        lambda: ["ruff", "mypy"],
    )

    config = LintroConfig(
        tools={"mypy": LintroToolConfig(enabled=False)},
    )

    with patch(
        "lintro.utils.execution.tool_configuration.get_config",
        return_value=config,
    ):
        result = get_tools_to_run(tools="ruff,mypy", action="check")

    assert_that(result.to_run).is_equal_to(["ruff"])
    assert_that(result.to_run).does_not_contain("mypy")
    skipped_names = [s.name for s in result.skipped]
    assert_that(skipped_names).contains("mypy")


def test_all_tools_disabled_returns_empty_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When all available tools are disabled, return empty to_run list."""
    from lintro.tools import tool_manager

    monkeypatch.setattr(
        tool_manager,
        "get_check_tools",
        lambda: ["ruff", "mypy"],
    )

    config = LintroConfig(
        tools={
            "ruff": LintroToolConfig(enabled=False),
            "mypy": LintroToolConfig(enabled=False),
        },
    )

    with patch(
        "lintro.utils.execution.tool_configuration.get_config",
        return_value=config,
    ):
        result = get_tools_to_run(tools=None, action="check")

    assert_that(result.to_run).is_empty()
    assert_that(result.skipped).is_length(2)


# =============================================================================
# Enabled tools
# =============================================================================


def test_enabled_tool_runs_normally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicitly enabled tool should run normally."""
    from lintro.tools import tool_manager

    monkeypatch.setattr(
        tool_manager,
        "get_check_tools",
        lambda: ["ruff", "mypy"],
    )

    config = LintroConfig(
        tools={
            "ruff": LintroToolConfig(enabled=True),
            "mypy": LintroToolConfig(enabled=False),
        },
    )

    with patch(
        "lintro.utils.execution.tool_configuration.get_config",
        return_value=config,
    ):
        result = get_tools_to_run(tools=None, action="check")

    assert_that(result.to_run).is_equal_to(["ruff"])


def test_tool_not_in_config_defaults_to_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tools not explicitly configured should default to enabled."""
    from lintro.tools import tool_manager

    monkeypatch.setattr(
        tool_manager,
        "get_check_tools",
        lambda: ["ruff", "mypy", "bandit"],
    )

    config = LintroConfig(
        tools={"mypy": LintroToolConfig(enabled=False)},
    )

    with patch(
        "lintro.utils.execution.tool_configuration.get_config",
        return_value=config,
    ):
        result = get_tools_to_run(tools=None, action="check")

    assert_that(result.to_run).contains("ruff", "bandit")
    assert_that(result.to_run).does_not_contain("mypy")


def test_empty_config_all_tools_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty config should have all tools enabled by default."""
    from lintro.tools import tool_manager

    monkeypatch.setattr(
        tool_manager,
        "get_check_tools",
        lambda: ["ruff", "mypy", "bandit"],
    )

    config = LintroConfig()

    with patch(
        "lintro.utils.execution.tool_configuration.get_config",
        return_value=config,
    ):
        result = get_tools_to_run(tools=None, action="check")

    assert_that(result.to_run).contains("ruff", "mypy", "bandit")
    assert_that(result.skipped).is_empty()


# =============================================================================
# Execution enabled_tools filter
# =============================================================================


def test_execution_enabled_tools_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only tools in execution.enabled_tools should run."""
    from lintro.tools import tool_manager

    monkeypatch.setattr(
        tool_manager,
        "get_check_tools",
        lambda: ["ruff", "mypy", "bandit"],
    )

    config = LintroConfig(
        execution=ExecutionConfig(enabled_tools=["ruff"]),
    )

    with patch(
        "lintro.utils.execution.tool_configuration.get_config",
        return_value=config,
    ):
        result = get_tools_to_run(tools=None, action="check")

    assert_that(result.to_run).is_equal_to(["ruff"])
    # mypy and bandit should be in skipped with "not in enabled_tools" reason
    skipped_names = [s.name for s in result.skipped]
    assert_that(skipped_names).contains("mypy", "bandit")
    for s in result.skipped:
        assert_that(s.reason).is_equal_to("not in enabled_tools")


def test_execution_enabled_tools_combined_with_tool_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both execution.enabled_tools and tools.enabled should be checked."""
    from lintro.tools import tool_manager

    monkeypatch.setattr(
        tool_manager,
        "get_check_tools",
        lambda: ["ruff", "mypy", "bandit"],
    )

    config = LintroConfig(
        execution=ExecutionConfig(enabled_tools=["ruff", "mypy"]),
        tools={"mypy": LintroToolConfig(enabled=False)},
    )

    with patch(
        "lintro.utils.execution.tool_configuration.get_config",
        return_value=config,
    ):
        result = get_tools_to_run(tools=None, action="check")

    assert_that(result.to_run).is_equal_to(["ruff"])


# =============================================================================
# Fix action
# =============================================================================


def test_disabled_tool_skipped_in_fix_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Disabled tool should be skipped for fix action."""
    from lintro.tools import tool_manager

    fake_ruff = _FakeTool(name="ruff", can_fix=True)
    fake_black = _FakeTool(name="black", can_fix=True)

    def mock_get_tool(name: str) -> _FakeTool:
        if name == "ruff":
            return fake_ruff
        if name == "black":
            return fake_black
        raise ValueError(f"Unknown tool: {name}")

    monkeypatch.setattr(
        tool_manager,
        "is_tool_registered",
        lambda name: name in ["ruff", "black"],
    )
    monkeypatch.setattr(tool_manager, "get_tool", mock_get_tool)
    monkeypatch.setattr(
        tool_manager,
        "get_tool_names",
        lambda: ["ruff", "black"],
    )

    config = LintroConfig(
        tools={"black": LintroToolConfig(enabled=False)},
    )

    with patch(
        "lintro.utils.execution.tool_configuration.get_config",
        return_value=config,
    ):
        result = get_tools_to_run(tools="ruff,black", action="fmt")

    assert_that(result.to_run).is_equal_to(["ruff"])
    assert_that(result.to_run).does_not_contain("black")
    skipped_names = [s.name for s in result.skipped]
    assert_that(skipped_names).contains("black")


# =============================================================================
# Case insensitivity
# =============================================================================


def test_disabled_tool_case_insensitive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tool enabled check should be case-insensitive."""
    from lintro.tools import tool_manager

    monkeypatch.setattr(
        tool_manager,
        "get_check_tools",
        lambda: ["ruff", "MyPy"],
    )

    config = LintroConfig(
        tools={"mypy": LintroToolConfig(enabled=False)},
    )

    with patch(
        "lintro.utils.execution.tool_configuration.get_config",
        return_value=config,
    ):
        result = get_tools_to_run(tools=None, action="check")

    assert_that(result.to_run).is_equal_to(["ruff"])
    assert_that(result.to_run).does_not_contain("MyPy")
    assert_that(result.to_run).does_not_contain("mypy")

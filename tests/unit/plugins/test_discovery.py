"""Unit tests for plugins/discovery module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.plugins.discovery import (
    BUILTIN_DEFINITIONS_PATH,
    ENTRY_POINT_GROUP,
    ENV_ENABLE_EXTERNAL_PLUGINS,
    discover_all_tools,
    discover_builtin_tools,
    discover_external_plugins,
    is_discovered,
    reset_discovery,
)


@pytest.fixture(autouse=True)
def clean_discovery_state() -> None:
    """Reset discovery state before each test to ensure clean state."""
    reset_discovery()


@pytest.fixture(autouse=True)
def _enable_external_plugins(monkeypatch: pytest.MonkeyPatch) -> None:
    """Opt in to external plugins for tests that exercise loading.

    External plugin loading is disabled by default (security). Most tests in
    this module assert on the loading path, so opt in via the env var here.
    Tests that verify default-deny behavior override this explicitly.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setenv(ENV_ENABLE_EXTERNAL_PLUGINS, "1")


# =============================================================================
# Tests for discover_builtin_tools
# =============================================================================


def test_discover_builtin_tools_loads_tools() -> None:
    """Load builtin tools from definitions directory."""
    result = discover_builtin_tools()
    assert_that(result).is_greater_than(0)


def test_discover_builtin_tools_skips_private_modules() -> None:
    """Skip modules starting with underscore."""
    # Verify __init__.py exists in the definitions path
    init_file = BUILTIN_DEFINITIONS_PATH / "__init__.py"
    assert_that(init_file.exists()).is_true()

    # Get count of non-private .py files
    non_private_files = [
        f for f in BUILTIN_DEFINITIONS_PATH.glob("*.py") if not f.name.startswith("_")
    ]
    expected_count = len(non_private_files)

    result = discover_builtin_tools()

    # Result should match non-private files, proving private files were skipped
    assert_that(result).is_equal_to(expected_count)


def test_discover_builtin_tools_handles_missing_path(tmp_path: Path) -> None:
    """Handle missing definitions path gracefully.

    Args:
        tmp_path: Temporary directory path for testing.
    """
    with patch(
        "lintro.plugins.discovery.BUILTIN_DEFINITIONS_PATH",
        tmp_path / "nonexistent",
    ):
        result = discover_builtin_tools()
        assert_that(result).is_equal_to(0)


# =============================================================================
# Tests for discover_external_plugins
# =============================================================================


def test_discover_external_plugins_handles_no_entry_points() -> None:
    """Handle case with no entry points."""
    with patch("importlib.metadata.entry_points", return_value=[]):
        result = discover_external_plugins()
        assert_that(result).is_equal_to(0)


def test_discover_external_plugins_handles_entry_point_error() -> None:
    """Handle entry point discovery error."""
    with patch(
        "importlib.metadata.entry_points",
        side_effect=TypeError("Entry point error"),
    ):
        result = discover_external_plugins()
        assert_that(result).is_equal_to(0)


@pytest.mark.parametrize(
    ("entry_point_name", "loaded_value", "description"),
    [
        ("non_class", "not a class", "string value instead of class"),
        ("function_ep", lambda: None, "function instead of class"),
        ("int_ep", 42, "integer instead of class"),
    ],
)
def test_discover_external_plugins_skips_non_class_entry_point(
    entry_point_name: str,
    loaded_value: object,
    description: str,
) -> None:
    """Skip entry points that don't point to classes ({description}).

    Args:
        entry_point_name: Name of the entry point.
        loaded_value: The value loaded from the entry point.
        description: Description of the test case.
    """
    mock_ep = MagicMock()
    mock_ep.name = entry_point_name
    mock_ep.load.return_value = loaded_value

    with patch("importlib.metadata.entry_points", return_value=[mock_ep]):
        result = discover_external_plugins()
        assert_that(result).is_equal_to(0)


def test_discover_external_plugins_skips_non_plugin_class() -> None:
    """Skip classes that don't implement LintroPlugin."""

    class NotAPlugin:
        pass

    mock_ep = MagicMock()
    mock_ep.name = "not_plugin"
    mock_ep.load.return_value = NotAPlugin

    with patch("importlib.metadata.entry_points", return_value=[mock_ep]):
        result = discover_external_plugins()
        assert_that(result).is_equal_to(0)


def test_discover_external_plugins_handles_load_error() -> None:
    """Handle error when loading entry point."""
    mock_ep = MagicMock()
    mock_ep.name = "error_plugin"
    mock_ep.load.side_effect = ImportError("Load error")

    with patch("importlib.metadata.entry_points", return_value=[mock_ep]):
        result = discover_external_plugins()
        assert_that(result).is_equal_to(0)


# =============================================================================
# Tests for external plugin trust model (opt-in, default-deny)
# =============================================================================


def test_external_plugins_disabled_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not load external plugins without explicit opt-in.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.delenv(ENV_ENABLE_EXTERNAL_PLUGINS, raising=False)
    monkeypatch.setattr(
        "lintro.plugins.discovery._load_plugins_config",
        lambda: {},
    )

    mock_ep = MagicMock()
    mock_ep.name = "evil"

    with patch(
        "importlib.metadata.entry_points",
        return_value=[mock_ep],
    ) as mock_entry_points:
        result = discover_external_plugins()

    assert_that(result).is_equal_to(0)
    # The entry point registry must not even be queried, and no plugin code
    # (ep.load) may be executed when loading is disabled.
    assert_that(mock_entry_points.called).is_false()
    assert_that(mock_ep.load.called).is_false()


def test_external_plugins_opt_in_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Load external plugins when the opt-in env var is set.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setenv(ENV_ENABLE_EXTERNAL_PLUGINS, "1")
    monkeypatch.setattr(
        "lintro.plugins.discovery._load_plugins_config",
        lambda: {},
    )

    mock_ep = MagicMock()
    mock_ep.name = "trusted_plugin"
    mock_ep.load.return_value = "not-a-class"

    with patch("importlib.metadata.entry_points", return_value=[mock_ep]):
        discover_external_plugins()

    # Opt-in reached the load path and executed the entry point.
    assert_that(mock_ep.load.called).is_true()


def test_allowlist_filters_untrusted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Load only allowlisted entry points and skip untrusted ones.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.delenv(ENV_ENABLE_EXTERNAL_PLUGINS, raising=False)
    monkeypatch.setattr(
        "lintro.plugins.discovery._load_plugins_config",
        lambda: {"trusted": ["a"]},
    )

    ep_a = MagicMock()
    ep_a.name = "a"
    ep_a.load.return_value = "not-a-class"
    ep_b = MagicMock()
    ep_b.name = "b"
    ep_b.load.return_value = "not-a-class"

    with patch("importlib.metadata.entry_points", return_value=[ep_a, ep_b]):
        discover_external_plugins()

    # Only the allowlisted entry point may be loaded/executed.
    assert_that(ep_a.load.called).is_true()
    assert_that(ep_b.load.called).is_false()


# =============================================================================
# Tests for discover_all_tools
# =============================================================================


def test_discover_all_tools_discovers_tools() -> None:
    """Discover all tools."""
    result = discover_all_tools()
    assert_that(result).is_greater_than(0)


def test_discover_all_tools_skips_if_already_discovered() -> None:
    """Skip discovery if already discovered."""
    first_result = discover_all_tools()
    assert_that(first_result).is_greater_than(0)

    # Second call should return 0 (skipped)
    second_result = discover_all_tools()
    assert_that(second_result).is_equal_to(0)


def test_discover_all_tools_force_rediscovery() -> None:
    """Force rediscovery when force=True."""
    first_result = discover_all_tools()
    assert_that(first_result).is_greater_than(0)

    # Force should re-discover
    forced_result = discover_all_tools(force=True)
    assert_that(forced_result).is_greater_than(0)


# =============================================================================
# Tests for is_discovered
# =============================================================================


def test_is_discovered_false_before_discovery() -> None:
    """Return False before discovery."""
    result = is_discovered()
    assert_that(result).is_false()


def test_is_discovered_true_after_discovery() -> None:
    """Return True after discovery."""
    discover_all_tools()
    result = is_discovered()
    assert_that(result).is_true()


# =============================================================================
# Tests for reset_discovery
# =============================================================================


def test_reset_discovery_resets_discovery_state() -> None:
    """Reset discovery state."""
    discover_all_tools()
    assert_that(is_discovered()).is_true()

    reset_discovery()
    result = is_discovered()
    assert_that(result).is_false()


# =============================================================================
# Tests for module constants
# =============================================================================


def test_builtin_definitions_path_exists() -> None:
    """Builtin definitions path exists."""
    assert_that(BUILTIN_DEFINITIONS_PATH.exists()).is_true()


def test_builtin_definitions_path_is_directory() -> None:
    """Builtin definitions path is a directory."""
    assert_that(BUILTIN_DEFINITIONS_PATH.is_dir()).is_true()


def test_entry_point_group_value() -> None:
    """Entry point group is correct."""
    assert_that(ENTRY_POINT_GROUP).is_equal_to("lintro.tools")

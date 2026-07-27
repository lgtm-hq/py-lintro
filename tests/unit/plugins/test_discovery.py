"""Unit tests for plugins/discovery module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that
from loguru import logger

from lintro.plugins.discovery import (
    BUILTIN_DEFINITIONS_PATH,
    ENTRY_POINT_GROUP,
    ENV_ENABLE_EXTERNAL_PLUGINS,
    _load_external_entry_point,
    discover_all_tools,
    discover_builtin_tools,
    discover_external_plugins,
    get_known_plugin_tool_names,
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


def test_malformed_yaml_config_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A malformed ``.lintro-config.yaml`` disables external plugins.

    A YAML parse error must not crash discovery or be swallowed into a
    load-everything state; it must fail closed and load no external plugins.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Temporary working directory.
    """
    monkeypatch.setenv(ENV_ENABLE_EXTERNAL_PLUGINS, "1")
    monkeypatch.chdir(tmp_path)
    # Unbalanced brackets: invalid YAML that raises yaml.YAMLError.
    (tmp_path / ".lintro-config.yaml").write_text(
        "plugins:\n  trusted: [unterminated\n",
        encoding="utf-8",
    )

    ep = MagicMock()
    ep.name = "evil"
    ep.load.return_value = "not-a-class"

    with patch("importlib.metadata.entry_points", return_value=[ep]):
        result = discover_external_plugins()

    assert_that(result).is_equal_to(0)
    assert_that(ep.load.called).is_false()


def test_malformed_pyproject_config_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A malformed ``pyproject.toml`` disables external plugins.

    When ``pyproject.toml`` is the only trust-config source and it cannot be
    parsed, discovery must fail closed rather than treat the allowlist as
    absent (which would load every discovered plugin).

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Temporary working directory.
    """
    monkeypatch.setenv(ENV_ENABLE_EXTERNAL_PLUGINS, "1")
    monkeypatch.chdir(tmp_path)
    # Invalid TOML: raises tomllib.TOMLDecodeError.
    (tmp_path / "pyproject.toml").write_text(
        "[tool.lintro.plugins]\ntrusted = [\n",
        encoding="utf-8",
    )

    ep = MagicMock()
    ep.name = "evil"
    ep.load.return_value = "not-a-class"

    with patch("importlib.metadata.entry_points", return_value=[ep]):
        result = discover_external_plugins()

    assert_that(result).is_equal_to(0)
    assert_that(ep.load.called).is_false()


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


# =============================================================================
# Tests for get_known_plugin_tool_names
# =============================================================================


def test_known_plugin_tool_names_reads_entry_point_metadata() -> None:
    """Advertised entry-point names are returned without importing plugins."""
    ep = MagicMock()
    ep.name = "My-Plugin"
    ep.load.side_effect = AssertionError("must not import the plugin")

    try:
        with patch("importlib.metadata.entry_points", return_value=[ep]):
            names = get_known_plugin_tool_names()
    finally:
        # The lookup is process-cached; drop the fake so later tests in the
        # session do not inherit a plugin that does not exist.
        reset_discovery()

    assert_that(names).contains("my-plugin")


def test_known_plugin_tool_names_does_not_trigger_discovery() -> None:
    """Asking for plugin names must not run a full discovery pass."""
    with patch("importlib.metadata.entry_points", return_value=[]):
        names = get_known_plugin_tool_names()

    assert_that(names).is_empty()
    assert_that(is_discovered()).is_false()


def test_known_plugin_tool_names_includes_registry_after_discovery() -> None:
    """Once discovery has run, registered tool names are included."""
    discover_all_tools()

    with patch("importlib.metadata.entry_points", return_value=[]):
        names = get_known_plugin_tool_names()

    assert_that(names).contains("ruff")


def test_known_plugin_tool_names_survives_metadata_backend_failure() -> None:
    """A broken metadata backend must not abort the caller (config loading).

    ``get_known_plugin_tool_names`` runs inside pyproject config conversion,
    so an unreadable dist-info directory has to degrade to "no plugin names"
    rather than take valid core configuration down with it.
    """
    with patch(
        "importlib.metadata.entry_points",
        side_effect=OSError("dist-info unreadable"),
    ):
        names = get_known_plugin_tool_names()

    assert_that(names).is_empty()


def test_entry_point_name_divergence_is_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A plugin registering under a different name than it advertises warns.

    Config keys are matched against the advertised entry-point name before
    discovery has run, so a divergent definition name would silently strand
    the user's config under a key nothing ever reads.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    instance = MagicMock()
    instance.definition.name = "acmelint"
    plugin_class = MagicMock(return_value=instance)

    ep = MagicMock()
    ep.name = "acme-tools"
    ep.load.return_value = plugin_class

    registry = MagicMock()
    registry.is_registered.return_value = False

    messages: list[str] = []
    monkeypatch.setattr(
        "lintro.plugins.discovery._validate_plugin_class",
        lambda ep, plugin_class: True,
    )
    monkeypatch.setattr("lintro.plugins.discovery.ToolRegistry", registry)
    handler_id = logger.add(
        lambda message: messages.append(str(message)),
        level="WARNING",
    )
    try:
        loaded = _load_external_entry_point(ep=ep)
    finally:
        logger.remove(handler_id)

    assert_that(loaded).is_equal_to(1)
    output = "".join(messages)
    assert_that(output).contains("acme-tools")
    assert_that(output).contains("[tool.lintro.acmelint]")


def test_matching_entry_point_name_stays_quiet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Spelling-only differences are not a divergence and must not warn.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    instance = MagicMock()
    instance.definition.name = "acme_lint"
    plugin_class = MagicMock(return_value=instance)

    ep = MagicMock()
    ep.name = "Acme-Lint"
    ep.load.return_value = plugin_class

    registry = MagicMock()
    registry.is_registered.return_value = False

    messages: list[str] = []
    monkeypatch.setattr(
        "lintro.plugins.discovery._validate_plugin_class",
        lambda ep, plugin_class: True,
    )
    monkeypatch.setattr("lintro.plugins.discovery.ToolRegistry", registry)
    handler_id = logger.add(
        lambda message: messages.append(str(message)),
        level="WARNING",
    )
    try:
        loaded = _load_external_entry_point(ep=ep)
    finally:
        logger.remove(handler_id)

    assert_that(loaded).is_equal_to(1)
    assert_that("".join(messages)).does_not_contain("registers the tool under")

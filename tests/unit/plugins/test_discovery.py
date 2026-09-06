"""Unit tests for plugins/discovery module."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that
from loguru import logger

import lintro.tools.definitions as definitions_package
from lintro.models.core.tool_result import ToolResult
from lintro.plugins._builtin_index import (
    BUILTIN_TOOL_MODULES,
    REGISTERING_TOOL_MODULES,
)
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.discovery import (
    BUILTIN_DEFINITIONS_PACKAGE,
    ENTRY_POINT_GROUP,
    ENV_ENABLE_EXTERNAL_PLUGINS,
    _load_external_entry_point,
    _module_names_from_package_scan,
    discover_all_tools,
    discover_builtin_tools,
    discover_external_plugins,
    get_builtin_module_names,
    get_known_plugin_tool_names,
    is_discovered,
    reset_discovery,
)
from lintro.plugins.protocol import LINTRO_PLUGIN_API_VERSION, ToolDefinition
from lintro.plugins.registry import ToolRegistry


def _make_external_plugin(*, tool_name: str) -> type[BaseToolPlugin]:
    """Build a well-formed third-party plugin class.

    Args:
        tool_name: Name the plugin's tool definition should report.

    Returns:
        A ``BaseToolPlugin`` subclass declaring a compatible API version.
    """

    @dataclass
    class _ExternalPlugin(BaseToolPlugin):
        LINTRO_PLUGIN_API_VERSION = LINTRO_PLUGIN_API_VERSION

        @property
        def definition(self) -> ToolDefinition:
            """Return the plugin's tool definition.

            Returns:
                The tool definition advertised by this fake plugin.
            """
            return ToolDefinition(
                name=tool_name,
                description="An external tool used in discovery tests",
                file_patterns=["*.fake"],
            )

        def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
            """Return a trivially successful result.

            Args:
                paths: Unused input paths.
                options: Unused runtime options.

            Returns:
                A successful ``ToolResult``.
            """
            return ToolResult(name=tool_name, success=True, issues_count=0)

    return _ExternalPlugin


@dataclass
class _FakeEntryPoint:
    """Minimal stand-in for ``importlib.metadata.EntryPoint``.

    Attributes:
        name: Entry-point name, which is also the tool name the backing plugin
            registers itself under.
        value: The ``module:attr`` target string discovery reports on failure.
        dist: Distribution object exposing ``.name``, or ``None``.
        load_count: How many times :meth:`load` has been invoked, so a test
            can assert the trust gate refused *before* importing the plugin.
    """

    name: str
    value: str = "fake_pkg.plugin:Plugin"
    dist: object | None = None
    load_count: int = 0

    def load(self) -> type[BaseToolPlugin]:
        """Return a well-formed plugin class registering ``self.name``.

        Counts its own invocations: the trust gate has to fail closed *before*
        importing third-party code, so "was never registered" is a weaker
        property than "was never loaded" (#2315).

        Returns:
            A ``BaseToolPlugin`` subclass whose tool is named after this entry
            point.
        """
        self.load_count += 1
        return _make_external_plugin(tool_name=self.name)


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
    module_names = get_builtin_module_names()
    assert_that([n for n in module_names if n.startswith("_")]).is_empty()

    result = discover_builtin_tools()

    assert_that(result).is_equal_to(len(module_names))


def test_discover_builtin_tools_without_known_modules() -> None:
    """Report zero loaded tools when no builtin module names are known."""
    with (
        patch("lintro.plugins.discovery.BUILTIN_TOOL_MODULES", ()),
        patch(
            "lintro.plugins.discovery._module_names_from_package_scan",
            return_value=set(),
        ),
    ):
        result = discover_builtin_tools()
        assert_that(result).is_equal_to(0)


def test_discover_builtin_tools_uses_index_without_source_dir() -> None:
    """Import builtin modules from the index when the package has no path.

    Mirrors a frozen Nuitka onefile binary, where the definitions source
    directory is never materialized and the package scan yields nothing.
    """
    with patch(
        "lintro.plugins.discovery._module_names_from_package_scan",
        return_value=set(),
    ):
        result = discover_builtin_tools()

    assert_that(result).is_equal_to(len(BUILTIN_TOOL_MODULES))
    assert_that(result).is_greater_than(0)


def test_module_names_from_package_scan_handles_unscannable_package() -> None:
    """Return an empty set when the definitions package exposes no path."""
    package = MagicMock()
    package.__path__ = []
    with patch("importlib.import_module", return_value=package):
        assert_that(_module_names_from_package_scan()).is_empty()


def test_module_names_from_package_scan_handles_import_error() -> None:
    """Degrade to an empty set when the definitions package cannot import."""
    with patch("importlib.import_module", side_effect=ImportError("boom")):
        assert_that(_module_names_from_package_scan()).is_empty()


def test_get_builtin_module_names_unions_index_and_scan() -> None:
    """Combine the generated index with modules found by the package scan."""
    with patch(
        "lintro.plugins.discovery._module_names_from_package_scan",
        return_value={"ruff", "not_yet_indexed"},
    ):
        names = get_builtin_module_names()

    assert_that(names).contains("not_yet_indexed")
    assert_that(names).contains(*BUILTIN_TOOL_MODULES)
    assert_that(list(names)).is_equal_to(sorted(names))


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

    entry_point = _FakeEntryPoint(name="trusted-plugin")

    with patch("importlib.metadata.entry_points", return_value=[entry_point]):
        registered = discover_external_plugins()

    assert_that(registered).is_equal_to(1)
    assert_that(ToolRegistry.is_registered("trusted-plugin")).is_true()


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
        lambda: {"trusted": ["allowed-plugin"]},
    )

    allowed = _FakeEntryPoint(name="allowed-plugin")
    untrusted = _FakeEntryPoint(name="untrusted-plugin")

    with patch("importlib.metadata.entry_points", return_value=[allowed, untrusted]):
        registered = discover_external_plugins()

    assert_that(registered).is_equal_to(1)
    assert_that(ToolRegistry.is_registered("allowed-plugin")).is_true()
    assert_that(ToolRegistry.is_registered("untrusted-plugin")).is_false()
    # The untrusted entry point must never be imported at all: refusing to
    # register it after loading would already have run its module-level code.
    assert_that(allowed.load_count).is_equal_to(1)
    assert_that(untrusted.load_count).is_equal_to(0)


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


def test_discover_external_plugins_respects_global_config_off(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Session discovery must not opt in via the real home ``plugins:`` section.

    Uses the real ``_load_plugins_config`` path (not a stub) with
    ``LINTRO_GLOBAL_CONFIG=off`` so a nested project cwd cannot inherit a home
    ``plugins.trusted`` allowlist during :func:`discover_external_plugins`.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    home = tmp_path / "home"
    home.mkdir()
    (home / ".lintro-config.yaml").write_text(
        "plugins:\n  trusted:\n    - home-plugin\n",
    )
    project = home / "project"
    project.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.chdir(project)
    monkeypatch.setenv("LINTRO_GLOBAL_CONFIG", "off")
    monkeypatch.delenv(ENV_ENABLE_EXTERNAL_PLUGINS, raising=False)

    mock_ep = MagicMock()
    mock_ep.name = "home-plugin"

    with patch(
        "importlib.metadata.entry_points",
        return_value=[mock_ep],
    ) as mock_entry_points:
        result = discover_external_plugins()

    assert_that(result).is_equal_to(0)
    assert_that(mock_entry_points.called).is_false()
    assert_that(mock_ep.load.called).is_false()


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


def test_builtin_definitions_package_matches_import_path() -> None:
    """The configured definitions package matches the real package."""
    assert_that(definitions_package.__name__).is_equal_to(BUILTIN_DEFINITIONS_PACKAGE)


def test_builtin_index_is_non_empty() -> None:
    """The generated builtin index lists tool modules."""
    assert_that(list(BUILTIN_TOOL_MODULES)).is_not_empty()


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


def test_shadowed_plugin_gets_no_divergence_advice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A plugin skipped for a name collision is not also given config advice.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    instance = MagicMock()
    instance.definition.name = "ruff"
    plugin_class = MagicMock(return_value=instance)

    ep = MagicMock()
    ep.name = "acme-tools"
    ep.load.return_value = plugin_class

    registry = MagicMock()
    registry.is_registered.return_value = True

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

    output = "".join(messages)
    assert_that(loaded).is_equal_to(0)
    assert_that(output).contains("avoid shadowing it")
    assert_that(output).does_not_contain("registers the tool under")


def test_registering_index_matches_the_real_registry() -> None:
    """The index's registering set is exactly the builtin registry.

    The generator detects registering modules via AST (``@register_tool`` as a
    Name or Attribute decorator). The binary smoke test treats that subset as
    the expected builtin tool set, so both over-counting and under-counting
    would make released binaries fail their own registry assertion — or worse,
    silently shrink the assertion. Equality catches both directions.
    """
    discover_builtin_tools()

    registered = {
        name.replace("-", "_")
        for name in ToolRegistry.get_names()
        if ToolRegistry.get_origin(name) == ToolRegistry.BUILTIN_ORIGIN
    }
    expected = {name.replace("-", "_") for name in REGISTERING_TOOL_MODULES}

    assert_that(sorted(registered)).is_equal_to(sorted(expected))

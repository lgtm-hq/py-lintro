"""Tests for third-party tool plugin discovery via Python entry points.

These tests exercise the ``lintro.tools`` entry-point contract end to end using
fake plugin distributions (entry points backed by in-memory plugin classes),
covering the happy path, failure isolation, API-version compatibility, builtin
name collisions, and per-invocation execution isolation.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.models.core.tool_result import ToolResult
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.discovery import (
    ENTRY_POINT_GROUP,
    ENV_ENABLE_EXTERNAL_PLUGINS,
    LEGACY_ENTRY_POINT_GROUP,
    discover_external_plugins,
    reset_discovery,
)
from lintro.plugins.protocol import (
    LINTRO_PLUGIN_API_VERSION,
    ToolDefinition,
)
from lintro.plugins.registry import ToolRegistry

# =============================================================================
# Fake plugin distribution helpers
# =============================================================================


class _FakeEntryPoint:
    """Minimal stand-in for ``importlib.metadata.EntryPoint``.

    Args:
        name: Entry-point name (the key in the ``lintro.tools`` group).
        loaded: Object returned by :meth:`load` (usually a plugin class).
        value: The ``module:attr`` target string.
        dist_name: Distribution name to expose via ``.dist.name``, or None.
        load_error: Exception to raise from :meth:`load` instead of returning.
    """

    def __init__(
        self,
        *,
        name: str,
        loaded: object,
        value: str = "fake_pkg.plugin:Plugin",
        dist_name: str | None = None,
        load_error: Exception | None = None,
    ) -> None:
        self.name = name
        self.value = value
        self._loaded = loaded
        self._load_error = load_error
        self.dist = (
            type("_Dist", (), {"name": dist_name})() if dist_name is not None else None
        )

    def load(self) -> object:
        """Return the target object or raise the configured error.

        Returns:
            The loaded plugin object.

        Raises:
            RuntimeError: When a configured ``load_error`` was provided.
        """
        if self._load_error is not None:
            raise RuntimeError(str(self._load_error)) from self._load_error
        return self._loaded


def _make_good_plugin(*, tool_name: str) -> type[BaseToolPlugin]:
    """Build a well-formed third-party plugin class.

    Args:
        tool_name: Name the plugin's tool definition should report.

    Returns:
        A ``BaseToolPlugin`` subclass declaring a compatible API version.
    """

    @dataclass
    class _GoodPlugin(BaseToolPlugin):
        LINTRO_PLUGIN_API_VERSION = LINTRO_PLUGIN_API_VERSION

        @property
        def definition(self) -> ToolDefinition:
            return ToolDefinition(
                name=tool_name,
                description="A well-formed external tool",
                file_patterns=["*.fake"],
                default_options={"flavor": "vanilla"},
            )

        def check(
            self,
            paths: list[str],
            options: dict[str, object],
        ) -> ToolResult:
            return ToolResult(name=tool_name, success=True, issues_count=0)

    return _GoodPlugin


def _make_versioned_plugin(*, tool_name: str, api_version: object) -> type:
    """Build a plugin class declaring a specific API version.

    Args:
        tool_name: Name the plugin's tool definition should report.
        api_version: Value assigned to ``LINTRO_PLUGIN_API_VERSION``.

    Returns:
        A ``BaseToolPlugin`` subclass with the given declared API version.
    """

    @dataclass
    class _VersionedPlugin(BaseToolPlugin):
        LINTRO_PLUGIN_API_VERSION = api_version

        @property
        def definition(self) -> ToolDefinition:
            return ToolDefinition(name=tool_name, description="Versioned tool")

        def check(
            self,
            paths: list[str],
            options: dict[str, object],
        ) -> ToolResult:
            return ToolResult(name=tool_name, success=True)

    return _VersionedPlugin


def _make_raising_plugin(*, tool_name: str) -> type:
    """Build a plugin class that raises when instantiated.

    Args:
        tool_name: Name reported by the (unreachable) definition.

    Returns:
        A ``BaseToolPlugin`` subclass whose ``definition`` raises, so
        construction fails during discovery's probe instantiation.
    """

    @dataclass
    class _RaisingPlugin(BaseToolPlugin):
        @property
        def definition(self) -> ToolDefinition:
            raise RuntimeError("boom during construction")

        def check(
            self,
            paths: list[str],
            options: dict[str, object],
        ) -> ToolResult:  # pragma: no cover - never reached
            return ToolResult(name=tool_name, success=True)

    return _RaisingPlugin


class _NotAPlugin:
    """A class that does not implement the LintroPlugin contract."""


@pytest.fixture(autouse=True)
def _enable_external_plugins(monkeypatch: pytest.MonkeyPatch) -> None:
    """Opt in to external plugin loading for entry-point tests.

    External plugin loading is default-deny under the trust model; these tests
    exercise the loading/validation path, so they enable it via the opt-in
    environment variable.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setenv(ENV_ENABLE_EXTERNAL_PLUGINS, "1")


@pytest.fixture(autouse=True)
def preserve_registry() -> Iterator[None]:
    """Snapshot and restore global registry + discovery state per test."""
    reset_discovery()
    tools = dict(ToolRegistry._tools)
    instances = dict(ToolRegistry._instances)
    origins = dict(ToolRegistry._origins)
    try:
        yield
    finally:
        ToolRegistry._tools = tools
        ToolRegistry._instances = instances
        ToolRegistry._origins = origins
        reset_discovery()


def _patch_entry_points(
    entry_points: list[_FakeEntryPoint],
    *,
    group: str = ENTRY_POINT_GROUP,
) -> AbstractContextManager[MagicMock]:
    """Patch ``importlib.metadata.entry_points`` to return fakes for one group.

    Args:
        entry_points: Fake entry points to expose.
        group: Entry-point group the fakes belong to; other groups return
            no entry points.

    Returns:
        A ``patch`` context manager.
    """
    target_group = group

    def _entry_points(*, group: str = "", **_: object) -> list[_FakeEntryPoint]:
        return entry_points if group == target_group else []

    return patch(
        "importlib.metadata.entry_points",
        side_effect=_entry_points,
    )


# =============================================================================
# Happy path
# =============================================================================


def test_well_formed_plugin_is_discovered_and_registered() -> None:
    """A valid entry-point plugin is discovered and appears in the registry."""
    ep = _FakeEntryPoint(
        name="ext-good",
        loaded=_make_good_plugin(tool_name="ext-good"),
        dist_name="lintro-ext-good",
    )

    with _patch_entry_points([ep]):
        loaded = discover_external_plugins()

    assert_that(loaded).is_equal_to(1)
    assert_that(ToolRegistry.is_registered("ext-good")).is_true()
    assert_that(ToolRegistry.get("ext-good").definition.name).is_equal_to("ext-good")


def test_plugin_origin_records_distribution_name() -> None:
    """Discovered plugin origin reflects the distribution package name."""
    ep = _FakeEntryPoint(
        name="ext-good",
        loaded=_make_good_plugin(tool_name="ext-good"),
        dist_name="lintro-ext-good",
    )

    with _patch_entry_points([ep]):
        discover_external_plugins()

    assert_that(ToolRegistry.get_origin("ext-good")).is_equal_to("lintro-ext-good")


def test_plugin_origin_falls_back_to_module_without_dist() -> None:
    """Origin falls back to the entry-point module when dist is unknown."""
    ep = _FakeEntryPoint(
        name="ext-good",
        loaded=_make_good_plugin(tool_name="ext-good"),
        value="my_pkg.plugin:ExtGood",
        dist_name=None,
    )

    with _patch_entry_points([ep]):
        discover_external_plugins()

    assert_that(ToolRegistry.get_origin("ext-good")).is_equal_to("my_pkg.plugin")


# =============================================================================
# list-tools origin annotation
# =============================================================================


def test_list_tools_shows_origin_for_builtin_and_external() -> None:
    """list-tools JSON reports builtin origin for core tools and pkg for plugin."""
    from lintro.cli_utils.commands.list_tools import list_tools
    from lintro.plugins.discovery import discover_builtin_tools

    discover_builtin_tools()
    ep = _FakeEntryPoint(
        name="ext-good",
        loaded=_make_good_plugin(tool_name="ext-good"),
        dist_name="lintro-ext-good",
    )
    with _patch_entry_points([ep]):
        discover_external_plugins()

    import io
    import json
    from contextlib import redirect_stdout

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        list_tools(output=None, show_conflicts=False, json_output=True)
    data = json.loads(buffer.getvalue())

    assert_that(data["ruff"]["origin"]).is_equal_to("builtin")
    assert_that(data["ext-good"]["origin"]).is_equal_to("lintro-ext-good")


# =============================================================================
# Failure isolation
# =============================================================================


def test_incompatible_api_version_is_skipped_not_raised() -> None:
    """A plugin declaring an incompatible API version is skipped with a warning."""
    ep = _FakeEntryPoint(
        name="ext-old",
        loaded=_make_versioned_plugin(
            tool_name="ext-old",
            api_version=LINTRO_PLUGIN_API_VERSION + 1,
        ),
        dist_name="lintro-ext-old",
    )

    with _patch_entry_points([ep]):
        loaded = discover_external_plugins()

    assert_that(loaded).is_equal_to(0)
    assert_that(ToolRegistry.is_registered("ext-old")).is_false()


def test_plugin_raising_on_instantiation_is_isolated() -> None:
    """A plugin that raises on construction is skipped without crashing others."""
    bad = _FakeEntryPoint(
        name="ext-bad",
        loaded=_make_raising_plugin(tool_name="ext-bad"),
    )
    good = _FakeEntryPoint(
        name="ext-good",
        loaded=_make_good_plugin(tool_name="ext-good"),
        dist_name="lintro-ext-good",
    )

    with _patch_entry_points([bad, good]):
        loaded = discover_external_plugins()

    # The good plugin still loads; the bad one is skipped, not raised.
    assert_that(loaded).is_equal_to(1)
    assert_that(ToolRegistry.is_registered("ext-good")).is_true()
    assert_that(ToolRegistry.is_registered("ext-bad")).is_false()


def test_class_not_implementing_contract_is_rejected() -> None:
    """A class missing the LintroPlugin surface is skipped."""
    ep = _FakeEntryPoint(name="ext-nope", loaded=_NotAPlugin)

    with _patch_entry_points([ep]):
        loaded = discover_external_plugins()

    assert_that(loaded).is_equal_to(0)
    assert_that(ToolRegistry.is_registered("ext-nope")).is_false()


def test_load_error_is_isolated() -> None:
    """An entry point that fails to import is skipped without crashing."""
    ep = _FakeEntryPoint(
        name="ext-broken",
        loaded=None,
        load_error=ImportError("no module named fake_pkg"),
    )

    with _patch_entry_points([ep]):
        loaded = discover_external_plugins()

    assert_that(loaded).is_equal_to(0)


# =============================================================================
# Builtin name collision
# =============================================================================


def test_name_collision_with_builtin_keeps_builtin() -> None:
    """A plugin colliding with a builtin tool name is skipped; builtin wins."""
    from lintro.plugins.discovery import discover_builtin_tools

    discover_builtin_tools()
    assert_that(ToolRegistry.is_registered("ruff")).is_true()

    ep = _FakeEntryPoint(
        name="ruff-clone",
        loaded=_make_good_plugin(tool_name="ruff"),
        dist_name="evil-ruff",
    )

    with _patch_entry_points([ep]):
        loaded = discover_external_plugins()

    assert_that(loaded).is_equal_to(0)
    # Builtin ruff retains its builtin origin, not the plugin's package name.
    assert_that(ToolRegistry.get_origin("ruff")).is_equal_to("builtin")


# =============================================================================
# Per-invocation isolation (copy_for_execution honored)
# =============================================================================


def test_external_plugin_honors_copy_for_execution() -> None:
    """External plugins inherit per-invocation option isolation."""
    ep = _FakeEntryPoint(
        name="ext-good",
        loaded=_make_good_plugin(tool_name="ext-good"),
        dist_name="lintro-ext-good",
    )
    with _patch_entry_points([ep]):
        discover_external_plugins()

    shared = ToolRegistry.get("ext-good")
    clone = shared.copy_for_execution()
    clone.set_options(exclude_patterns=["only_on_clone"])
    clone.options["flavor"] = "chocolate"

    # Mutating the clone must not leak into the shared registry singleton.
    assert_that(clone.exclude_patterns).contains("only_on_clone")
    assert_that(shared.exclude_patterns).does_not_contain("only_on_clone")
    assert_that(shared.options["flavor"]).is_equal_to("vanilla")
    assert_that(clone.options["flavor"]).is_equal_to("chocolate")


# =============================================================================
# Legacy entry-point group compatibility
# =============================================================================


def test_legacy_group_plugin_is_discovered() -> None:
    """A plugin registered under the deprecated group is still loaded."""
    ep = _FakeEntryPoint(
        name="ext-legacy",
        loaded=_make_good_plugin(tool_name="ext-legacy"),
        dist_name="lintro-ext-legacy",
    )
    with _patch_entry_points([ep], group=LEGACY_ENTRY_POINT_GROUP):
        loaded = discover_external_plugins()

    assert_that(loaded).is_equal_to(1)
    assert_that(ToolRegistry.is_registered("ext-legacy")).is_true()


def test_plugin_in_both_groups_loads_once() -> None:
    """An entry point advertised in both groups registers exactly once."""
    plugin = _make_good_plugin(tool_name="ext-both")
    primary = _FakeEntryPoint(
        name="ext-both",
        loaded=plugin,
        value="both_pkg.plugin:Plugin",
    )
    legacy = _FakeEntryPoint(
        name="ext-both",
        loaded=plugin,
        value="both_pkg.plugin:Plugin",
    )

    def _entry_points(*, group: str = "", **_: object) -> list[_FakeEntryPoint]:
        if group == ENTRY_POINT_GROUP:
            return [primary]
        if group == LEGACY_ENTRY_POINT_GROUP:
            return [legacy]
        return []

    with patch("importlib.metadata.entry_points", side_effect=_entry_points):
        loaded = discover_external_plugins()

    assert_that(loaded).is_equal_to(1)
    assert_that(ToolRegistry.is_registered("ext-both")).is_true()

"""Tests for third-party tool plugin discovery via Python entry points.

These tests exercise the ``lintro.tools`` entry-point contract end to end using
fake plugin distributions (entry points backed by in-memory plugin classes),
covering the happy path, failure isolation, API-version compatibility, builtin
name collisions, and per-invocation execution isolation.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that
from click.testing import CliRunner
from loguru import logger

from lintro.models.core.tool_result import ToolResult
from lintro.parsers.base_issue import BaseIssue
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.discovery import (
    ENTRY_POINT_GROUP,
    LEGACY_ENTRY_POINT_GROUP,
    discover_external_plugins,
    reset_discovery,
)
from lintro.plugins.protocol import (
    LINTRO_PLUGIN_API_VERSION,
    ToolDefinition,
    is_compatible_api_version,
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
                in_process=True,
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


def _make_raising_plugin(
    *,
    tool_name: str,
    api_version: object = LINTRO_PLUGIN_API_VERSION,
) -> type:
    """Build a plugin class that raises when instantiated.

    Args:
        tool_name: Name reported by the (unreachable) definition.
        api_version: Declared plugin contract version.

    Returns:
        A ``BaseToolPlugin`` subclass whose ``definition`` raises, so
        construction fails during discovery's probe instantiation.
    """

    @dataclass
    class _RaisingPlugin(BaseToolPlugin):
        LINTRO_PLUGIN_API_VERSION = api_version

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
def enable_external_plugins() -> Iterator[None]:
    """Opt in to external plugin loading for entry-point discovery tests.

    External plugin loading is opt-in and default-deny (see the plugin trust
    model). These tests exercise the discovery/registration mechanics with a
    permissive trust decision (enabled, no allowlist) so they focus on the
    entry-point contract rather than the trust gate, which is covered
    separately in ``test_discovery``.
    """
    with patch(
        "lintro.plugins.discovery._resolve_plugin_trust",
        return_value=(True, None),
    ):
        yield


@pytest.fixture(autouse=True)
def preserve_discovery() -> Iterator[None]:
    """Reset the memoised discovery state around each test.

    The registry itself is snapshotted by the root ``_isolate_plugin_registry``
    autouse fixture (#2315); only the discovery memo needs clearing here.

    Yields:
        None: Resets discovery before and after the test.
    """
    reset_discovery()
    try:
        yield
    finally:
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


@pytest.mark.parametrize("in_process", [None, True, False])
@pytest.mark.parametrize("strict", [False, True])
def test_versionless_api_v2_plugin_requires_execution_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    in_process: bool | None,
    strict: bool,
) -> None:
    """An ambiguous v2 plugin fails visibly; explicit modes execute or degrade."""
    from lintro.cli import cli
    from lintro.tools.core import snapshots

    name = "ext-probe-contract"

    class _PreparedPlugin(BaseToolPlugin):
        LINTRO_PLUGIN_API_VERSION = 2

        @property
        def definition(self) -> ToolDefinition:
            return ToolDefinition(
                name=name,
                description="API-v2 execution contract fixture",
                in_process=in_process,
                file_patterns=["*.fake"],
            )

        def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
            prepared = self.prepare(paths, options)
            if isinstance(prepared, ToolResult):
                return prepared
            return ToolResult(
                name=name,
                success=False,
                issues_count=1,
                issues=[
                    BaseIssue(file=prepared.files[0], message="in-process finding"),
                ],
            )

    monkeypatch.chdir(tmp_path)
    (tmp_path / "target.fake").write_text("content\n")
    (tmp_path / ".lintro-config.yaml").write_text(
        f"execution:\n  strict_missing_tools: {str(strict).lower()}\n",
    )
    monkeypatch.setattr(
        snapshots,
        "get_executable_command",
        lambda *a, **kw: [str(tmp_path / "absent-executable")],
    )
    ep = _FakeEntryPoint(name=name, loaded=_PreparedPlugin)
    with _patch_entry_points([ep]):
        assert_that(discover_external_plugins()).is_equal_to(1)
        result = CliRunner().invoke(
            cli,
            [
                "chk",
                "target.fake",
                "--tools",
                name,
                "--output-format",
                "json",
                "--output",
                "report.json",
            ],
        )
    row = json.loads((tmp_path / "report.json").read_text())["results"][0]
    assert_that(result.exit_code).is_equal_to(
        1 if in_process is not False else int(strict),
    )
    if in_process is None:
        assert_that(row["status"]).is_equal_to("failed")
        assert_that(row["output"]).contains("in_process=True", "in_process=False")
    elif in_process:
        assert_that(row["issues"][0]["message"]).is_equal_to("in-process finding")
        assert_that(row["issues_count"]).is_equal_to(1)
    else:
        assert_that(row["status"]).is_equal_to("unavailable")
        assert_that(row["success"]).is_equal_to(not strict)


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
    with (
        redirect_stdout(buffer),
        patch("lintro.tools.core.snapshots.probe_all_tools", return_value={}),
    ):
        list_tools(output=None, json_output=True)
    data = json.loads(buffer.getvalue())

    assert_that(data["ruff"]["origin"]).is_equal_to("builtin")
    assert_that(data["ext-good"]["origin"]).is_equal_to("lintro-ext-good")


# =============================================================================
# Failure isolation
# =============================================================================


@pytest.mark.parametrize("declared", [1, None, True, "2", 3])
def test_legacy_plugin_is_rejected_before_instantiation(declared: object) -> None:
    """Reject old and undeclared contracts before any plugin lifecycle runs.

    Args:
        declared: Incompatible version, or None for an absent declaration.
    """
    plugin = _make_raising_plugin(tool_name="ext-legacy", api_version=declared)
    if declared is None:
        delattr(plugin, "LINTRO_PLUGIN_API_VERSION")
    ep = _FakeEntryPoint(name="ext-legacy", loaded=plugin)
    good = _FakeEntryPoint(
        name="ext-current",
        loaded=_make_good_plugin(tool_name="ext-current"),
    )
    messages: list[str] = []
    handler_id = logger.add(lambda message: messages.append(str(message)))
    try:
        with _patch_entry_points([ep, good]):
            loaded = discover_external_plugins()
    finally:
        logger.remove(handler_id)

    assert_that(LINTRO_PLUGIN_API_VERSION).is_equal_to(2)
    assert_that(is_compatible_api_version(declared)).is_false()
    assert_that(loaded).is_equal_to(1)
    assert_that(ToolRegistry.is_registered("ext-legacy")).is_false()
    assert_that(ToolRegistry.get("ext-current").check([], {}).success).is_true()
    output = "".join(messages)
    assert_that(output).contains(
        "ext-legacy",
        f"version {declared!r}",
        "skipping before instantiation",
        "Upgrade the plugin",
        "LINTRO_PLUGIN_API_VERSION = 2",
        "docs/plugins.md#migrating-from-plugin-api-v1-to-v2",
    )
    assert_that(output).does_not_contain("boom during construction")


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

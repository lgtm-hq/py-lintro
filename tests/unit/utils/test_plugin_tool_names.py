"""Tests for the config-facing plugin tool-name lookup (#1305)."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from assertpy import assert_that

from lintro.utils import plugin_tool_names


@dataclass(frozen=True)
class _FakeEntryPoint:
    """Minimal stand-in for an ``importlib.metadata`` entry point."""

    name: str


@pytest.fixture(autouse=True)
def clean_sources() -> Iterator[None]:
    """Snapshot and restore the module-level source list and cache.

    Yields:
        None: Control returns to the test with a pristine source list.
    """
    saved = list(plugin_tool_names._EXTRA_NAME_SOURCES)
    plugin_tool_names._EXTRA_NAME_SOURCES.clear()
    plugin_tool_names.reset_plugin_tool_name_cache()
    try:
        yield
    finally:
        plugin_tool_names._EXTRA_NAME_SOURCES[:] = saved
        plugin_tool_names.reset_plugin_tool_name_cache()


def test_registered_sources_contribute_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A registered source widens the known-name set.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setattr(
        "importlib.metadata.entry_points",
        lambda **_: [_FakeEntryPoint(name="Acme-Lint")],
    )
    plugin_tool_names.reset_plugin_tool_name_cache()
    plugin_tool_names.register_tool_name_source(lambda: frozenset({"registered"}))

    names = plugin_tool_names.known_plugin_tool_names()

    assert_that(names).contains("acme-lint")
    assert_that(names).contains("registered")


def test_registration_is_idempotent() -> None:
    """Registering the same source twice does not stack duplicates."""
    before = len(plugin_tool_names._EXTRA_NAME_SOURCES)

    def source() -> frozenset[str]:
        """Return a fixed name set.

        Returns:
            frozenset[str]: A single fake tool name.
        """
        return frozenset({"dup"})

    plugin_tool_names.register_tool_name_source(source)
    plugin_tool_names.register_tool_name_source(source)

    assert_that(len(plugin_tool_names._EXTRA_NAME_SOURCES)).is_equal_to(before + 1)


def test_broken_entry_point_metadata_degrades_to_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing metadata backend yields no names instead of raising.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """

    def boom(**_: object) -> list[object]:
        """Raise as a broken metadata finder would.

        Args:
            **_: Ignored keyword arguments (the group name).

        Returns:
            list[object]: Never returns.

        Raises:
            RuntimeError: Always.
        """
        raise RuntimeError("unreadable dist-info")

    monkeypatch.setattr("importlib.metadata.entry_points", boom)
    plugin_tool_names.reset_plugin_tool_name_cache()

    assert_that(plugin_tool_names.advertised_plugin_tool_names()).is_empty()


def test_discovery_contributes_registry_names_after_discovery() -> None:
    """Registry tool names reach the config-facing lookup once discovery runs.

    Asserts the behaviour rather than the identity of the registered callable:
    a source that always returned an empty set would pass an identity check.
    """
    from lintro.plugins import discovery

    plugin_tool_names._EXTRA_NAME_SOURCES[:] = [discovery._registered_tool_names]
    discovery.discover_all_tools(force=True)

    names = plugin_tool_names.known_plugin_tool_names()

    # `ruff` is a builtin, so it can only reach the config-facing lookup
    # through the source `lintro.plugins.discovery` registers at import time.
    assert_that(names).contains("ruff")

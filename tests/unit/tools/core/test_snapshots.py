"""Tests for cached tool capability probing (issue #1244)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.tools.core.snapshots import (
    DEFAULT_SNAPSHOT_TTL_SECONDS,
    ToolCapabilities,
    ToolSnapshot,
    clear_snapshot_cache,
    get_tool_snapshot,
    probe_all_tools,
    probe_tool,
    set_force_fresh_probes,
    snapshot_to_unavailable_result,
)


@pytest.fixture(autouse=True)
def _reset_snapshot_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate snapshot cache to a temp directory for every test."""
    monkeypatch.chdir(tmp_path)
    clear_snapshot_cache(cache_root=tmp_path)
    set_force_fresh_probes(False)
    yield
    clear_snapshot_cache(cache_root=tmp_path)
    set_force_fresh_probes(False)


def _fake_snapshot(
    name: str = "ruff",
    *,
    available: bool = True,
    version: str | None = "0.14.0",
    binary_path: str = "/usr/bin/ruff",
    binary_mtime: float = 100.0,
    probe_error: str | None = None,
) -> ToolSnapshot:
    """Build a ToolSnapshot for tests."""
    return ToolSnapshot(
        name=name,
        available=available,
        version=version,
        capabilities=ToolCapabilities(can_fix=True, config_found=False),
        probe_error=probe_error,
        remediation_hint="Install ruff",
        binary_path=binary_path if available else "",
        binary_mtime=binary_mtime if available else 0.0,
        version_check_passed=available,
        min_version="0.1.0",
    )


def test_tool_snapshot_roundtrip_dict() -> None:
    """ToolSnapshot serializes and deserializes without data loss."""
    snap = _fake_snapshot()
    restored = ToolSnapshot.from_dict(snap.to_dict())
    assert_that(restored.name).is_equal_to(snap.name)
    assert_that(restored.available).is_true()
    assert_that(restored.version).is_equal_to("0.14.0")
    assert_that(restored.capabilities.can_fix).is_true()
    assert_that(restored.binary_mtime).is_equal_to(100.0)


def test_cache_hit_within_ttl_skips_reprobe(tmp_path: Path) -> None:
    """Second probe_all_tools within TTL must not re-run subprocess probes."""
    call_count = {"n": 0}

    def fake_probe(name: str, *, search_root: Path | None = None) -> ToolSnapshot:
        call_count["n"] += 1
        binary = tmp_path / f"{name}.bin"
        binary.write_text("x")
        return _fake_snapshot(
            name,
            binary_path=str(binary),
            binary_mtime=binary.stat().st_mtime,
        )

    with patch("lintro.tools.core.snapshots.probe_tool", side_effect=fake_probe):
        with patch(
            "lintro.plugins.discovery.discover_all_tools",
            return_value=None,
        ):
            with patch(
                "lintro.plugins.registry.ToolRegistry.get_names",
                return_value=["ruff", "black"],
            ):
                first = probe_all_tools(cache_root=tmp_path)
                second = probe_all_tools(cache_root=tmp_path)

    assert_that(call_count["n"]).is_equal_to(2)
    assert_that(first).contains_key("ruff", "black")
    assert_that(second["ruff"].version).is_equal_to(first["ruff"].version)
    cache_file = tmp_path / ".lintro-cache" / "tool-snapshots.json"
    assert_that(cache_file.exists()).is_true()


def test_mtime_bump_invalidates_cache(tmp_path: Path) -> None:
    """Changing binary mtime invalidates the cached snapshot for that tool."""
    from lintro import __version__ as lintro_version

    binary = tmp_path / "ruff.bin"
    binary.write_text("v1")
    snap = _fake_snapshot(
        "ruff",
        binary_path=str(binary),
        binary_mtime=binary.stat().st_mtime,
    )
    cache_file = tmp_path / ".lintro-cache" / "tool-snapshots.json"
    cache_file.parent.mkdir(parents=True)
    cache_file.write_text(
        json.dumps(
            {
                "lintro_version": lintro_version,
                "probed_at": time.time(),
                "ttl_seconds": DEFAULT_SNAPSHOT_TTL_SECONDS,
                "snapshots": {"ruff": snap.to_dict()},
            },
        ),
        encoding="utf-8",
    )

    # Bump mtime so the cache key no longer matches.
    time.sleep(0.05)
    binary.write_text("v2")

    call_count = {"n": 0}

    def fake_probe(name: str, *, search_root: Path | None = None) -> ToolSnapshot:
        call_count["n"] += 1
        return _fake_snapshot(
            name,
            version="0.15.0",
            binary_path=str(binary),
            binary_mtime=binary.stat().st_mtime,
        )

    with patch("lintro.tools.core.snapshots.probe_tool", side_effect=fake_probe):
        with patch(
            "lintro.plugins.discovery.discover_all_tools",
            return_value=None,
        ):
            with patch(
                "lintro.plugins.registry.ToolRegistry.get_names",
                return_value=["ruff"],
            ):
                result = probe_all_tools(cache_root=tmp_path)

    assert_that(call_count["n"]).is_equal_to(1)
    assert_that(result["ruff"].version).is_equal_to("0.15.0")


def test_missing_binary_yields_unavailable_snapshot() -> None:
    """Missing binary produces available=false with remediation hint."""
    definition = MagicMock()
    definition.name = "missingtool"
    definition.can_fix = False
    definition.native_configs = []
    definition.version_command = ["missingtool", "--version"]

    plugin = MagicMock()
    plugin.definition = definition

    with (
        patch("lintro.plugins.registry.ToolRegistry.get", return_value=plugin),
        patch(
            "lintro.plugins.execution_preparation.get_executable_command",
            return_value=["missingtool"],
        ),
        patch("shutil.which", return_value=None),
        patch(
            "lintro.tools.core.version_checking.get_install_hints",
            return_value={"missingtool": "Install missingtool via brew"},
        ),
    ):
        snap = probe_tool("missingtool")

    assert_that(snap.available).is_false()
    assert_that(snap.probe_error).contains("not found in PATH")
    assert_that(snap.remediation_hint).contains("Install missingtool")

    result = snapshot_to_unavailable_result(snap, strict=False)
    assert_that(result.unavailable).is_true()
    assert_that(result.success).is_true()
    assert_that(result.output).contains("unavailable")


def test_strict_missing_marks_unavailable_as_failure() -> None:
    """strict_missing_tools makes unavailable results fail the run."""
    snap = _fake_snapshot(
        available=False,
        version=None,
        probe_error="not found",
        binary_path="",
        binary_mtime=0.0,
    )
    result = snapshot_to_unavailable_result(snap, strict=True)
    assert_that(result.unavailable).is_true()
    assert_that(result.success).is_false()


def test_corrupt_cache_recovers_with_reprobe(tmp_path: Path) -> None:
    """Corrupt cache file is deleted and probing proceeds."""
    cache_file = tmp_path / ".lintro-cache" / "tool-snapshots.json"
    cache_file.parent.mkdir(parents=True)
    cache_file.write_text("{not-json", encoding="utf-8")

    def fake_probe(name: str, *, search_root: Path | None = None) -> ToolSnapshot:
        binary = tmp_path / f"{name}.bin"
        binary.write_text("x")
        return _fake_snapshot(
            name,
            binary_path=str(binary),
            binary_mtime=binary.stat().st_mtime,
        )

    with patch("lintro.tools.core.snapshots.probe_tool", side_effect=fake_probe):
        with patch(
            "lintro.plugins.discovery.discover_all_tools",
            return_value=None,
        ):
            with patch(
                "lintro.plugins.registry.ToolRegistry.get_names",
                return_value=["ruff"],
            ):
                result = probe_all_tools(cache_root=tmp_path)

    assert_that(result).contains_key("ruff")
    assert_that(result["ruff"].available).is_true()
    # Cache should have been rewritten as valid JSON
    data = json.loads(cache_file.read_text(encoding="utf-8"))
    assert_that(data).contains_key("snapshots")


def test_parallel_probe_correctness(tmp_path: Path) -> None:
    """Parallel probing returns a snapshot for every requested tool."""
    seen: list[str] = []

    def fake_probe(name: str, *, search_root: Path | None = None) -> ToolSnapshot:
        seen.append(name)
        binary = tmp_path / f"{name}.bin"
        binary.write_text(name)
        return _fake_snapshot(
            name,
            binary_path=str(binary),
            binary_mtime=binary.stat().st_mtime,
        )

    names = [f"tool{i}" for i in range(8)]
    with patch("lintro.tools.core.snapshots.probe_tool", side_effect=fake_probe):
        with patch(
            "lintro.plugins.discovery.discover_all_tools",
            return_value=None,
        ):
            with patch(
                "lintro.plugins.registry.ToolRegistry.get_names",
                return_value=names,
            ):
                result = probe_all_tools(cache_root=tmp_path, force=True)

    assert_that(sorted(seen)).is_equal_to(sorted(names))
    assert_that(sorted(result.keys())).is_equal_to(sorted(names))
    for name in names:
        assert_that(result[name].available).is_true()


def test_force_fresh_bypasses_memory_cache(tmp_path: Path) -> None:
    """force=True re-probes even when memory cache is warm."""
    call_count = {"n": 0}

    def fake_probe(name: str, *, search_root: Path | None = None) -> ToolSnapshot:
        call_count["n"] += 1
        binary = tmp_path / f"{name}.bin"
        binary.write_text("x")
        return _fake_snapshot(
            name,
            version=f"0.{call_count['n']}.0",
            binary_path=str(binary),
            binary_mtime=binary.stat().st_mtime,
        )

    with patch("lintro.tools.core.snapshots.probe_tool", side_effect=fake_probe):
        with patch(
            "lintro.plugins.discovery.discover_all_tools",
            return_value=None,
        ):
            with patch(
                "lintro.plugins.registry.ToolRegistry.get_names",
                return_value=["ruff"],
            ):
                probe_all_tools(cache_root=tmp_path)
                forced = probe_all_tools(cache_root=tmp_path, force=True)

    assert_that(call_count["n"]).is_equal_to(2)
    assert_that(forced["ruff"].version).is_equal_to("0.2.0")


def test_get_tool_snapshot_returns_named_entry(tmp_path: Path) -> None:
    """get_tool_snapshot returns the snapshot for the requested tool."""

    def fake_probe(name: str, *, search_root: Path | None = None) -> ToolSnapshot:
        binary = tmp_path / f"{name}.bin"
        binary.write_text("x")
        return _fake_snapshot(
            name,
            binary_path=str(binary),
            binary_mtime=binary.stat().st_mtime,
        )

    with patch("lintro.tools.core.snapshots.probe_tool", side_effect=fake_probe):
        with patch(
            "lintro.plugins.discovery.discover_all_tools",
            return_value=None,
        ):
            with patch(
                "lintro.plugins.registry.ToolRegistry.get_names",
                return_value=["ruff"],
            ):
                snap = get_tool_snapshot("ruff", cache_root=tmp_path)

    assert_that(snap.name).is_equal_to("ruff")
    assert_that(snap.available).is_true()


def test_verify_tool_version_uses_unavailable_snapshot() -> None:
    """verify_tool_version returns unavailable result when snapshot is down."""
    from lintro.plugins.execution_preparation import verify_tool_version
    from lintro.plugins.protocol import ToolDefinition

    snap = _fake_snapshot(
        "ghost",
        available=False,
        version=None,
        probe_error="ghost not found in PATH",
        binary_path="",
        binary_mtime=0.0,
    )
    definition = ToolDefinition(name="ghost", description="missing")

    with (
        patch(
            "lintro.tools.core.snapshots.get_tool_snapshot",
            return_value=snap,
        ),
        patch(
            "lintro.tools.core.snapshots.is_strict_missing_tools",
            return_value=False,
        ),
    ):
        result = verify_tool_version(definition)

    assert_that(result).is_not_none()
    assert_that(result.unavailable).is_true()
    assert_that(result.success).is_true()


def test_serialize_tool_result_emits_unavailable_status() -> None:
    """JSON serialization includes status=unavailable for degraded tools."""
    from lintro.enums.action import Action
    from lintro.models.core.tool_result import ToolResult
    from lintro.utils.json_output import serialize_tool_result

    result = ToolResult(
        name="ghost",
        success=True,
        output="ghost unavailable: not found",
        unavailable=True,
        skip_reason="not found",
    )
    data: dict[str, Any] = serialize_tool_result(result, action=Action.CHECK)
    assert_that(data["status"]).is_equal_to("unavailable")
    assert_that(data["unavailable"]).is_true()

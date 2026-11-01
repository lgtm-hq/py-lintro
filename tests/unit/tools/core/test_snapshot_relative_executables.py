"""Relative executable probes and cache keys follow the execution directory."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from lintro.plugins.protocol import ToolDefinition
from lintro.tools.core import snapshots

_COMMAND = "./bin/custom-linter"


@pytest.fixture(params=["versionless", "versioned", "manifest"])
def local_command(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> str:
    """Provide relative plugin and manifest commands, retaining real subprocesses."""
    mode = request.param
    definition = ToolDefinition(
        "ruff",
        "project-local linter",
        version_command=None if mode == "versionless" else [_COMMAND, "--version"],
    )
    monkeypatch.setattr(
        "lintro.tools.tool_manager.is_tool_registered",
        lambda name: mode != "manifest",
    )
    monkeypatch.setattr(
        "lintro.tools.tool_manager.get_tool",
        lambda name: SimpleNamespace(definition=definition),
    )
    monkeypatch.setattr(
        snapshots,
        "_manifest_tool",
        lambda name: SimpleNamespace(version_command=[_COMMAND, "--version"]),
    )
    monkeypatch.setattr(
        snapshots.CommandBuilderRegistry,
        "get_command",
        lambda *args: [_COMMAND],
    )
    return mode


def _write_linter(root: Path, version: str) -> Path:
    """Create a real executable with deterministic version output."""
    binary = root / "bin" / "custom-linter"
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_text(f"#!/bin/sh\necho 'ruff {version}'\n", encoding="utf-8")
    binary.chmod(0o755)
    return binary


def test_relative_executable_probe_uses_execution_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    local_command: str,
) -> None:
    """A tool outside process cwd has the same availability and binary identity."""
    root = tmp_path / "project"
    binary = _write_linter(root, "99.0.0")
    monkeypatch.chdir(tmp_path)

    result = snapshots.probe_tool("ruff", search_root=root)

    assert result.available is True
    assert result.version_check_passed is True
    assert result.binary_path == str(binary)
    assert result.binary_mtime == binary.stat().st_mtime
    assert result.version == (None if local_command == "versionless" else "99.0.0")


def test_relative_executable_cache_freshness_uses_execution_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    local_command: str,
) -> None:
    """Correct cached evidence stays fresh even when process cwd has another tool."""
    root = tmp_path / "project"
    binary = _write_linter(root, "99.0.0")
    _write_linter(tmp_path, "1.0.0")
    monkeypatch.chdir(tmp_path)
    command = (_COMMAND, "--version") if local_command == "manifest" else (_COMMAND,)
    cached = snapshots.ToolSnapshot(
        name="ruff",
        available=True,
        binary_path=str(binary),
        binary_mtime=binary.stat().st_mtime,
        command=command,
    )

    assert snapshots._is_cached_snapshot_fresh(cached, search_root=root) is True


@pytest.mark.parametrize("hydrate", [False, True], ids=["memory", "disk"])
def test_relative_executable_cache_tracks_each_execution_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    local_command: str,
    hydrate: bool,
) -> None:
    """Hits, upgrades, removal and installation are isolated by execution root."""
    roots = [tmp_path / "first", tmp_path / "second"]
    versions = ["99.0.0", "98.0.0"]
    binaries = [_write_linter(r, v) for r, v in zip(roots, versions, strict=True)]
    monkeypatch.chdir(tmp_path)
    clock = [1000.0]
    monkeypatch.setattr(snapshots.time, "time", lambda: clock[0])
    initial = [snapshots.get_tool_snapshot("ruff", cache_root=root) for root in roots]
    for binary, result, version in zip(binaries, initial, versions, strict=True):
        assert result.available is True
        assert result.binary_path == str(binary)
        assert result.version == (None if local_command == "versionless" else version)
    clock[0] += 1
    if hydrate:
        snapshots.clear_snapshot_cache(include_disk=False)
    assert [snapshots.get_tool_snapshot("ruff", cache_root=r) for r in roots] == initial

    _write_linter(roots[0], "100.0.0")
    changed_mtime = initial[0].binary_mtime + 10
    os.utime(binaries[0], (changed_mtime, changed_mtime))
    if hydrate:
        snapshots.clear_snapshot_cache(include_disk=False)
    upgraded = snapshots.get_tool_snapshot("ruff", cache_root=roots[0])
    assert upgraded.binary_mtime == changed_mtime
    assert upgraded.probed_at == clock[0]
    assert upgraded.version == (None if local_command == "versionless" else "100.0.0")
    assert snapshots.get_tool_snapshot("ruff", cache_root=roots[1]) == initial[1]

    binaries[0].unlink()
    if hydrate:
        snapshots.clear_snapshot_cache(include_disk=False)
    assert snapshots.get_tool_snapshot("ruff", cache_root=roots[0]).available is False
    _write_linter(roots[0], "101.0.0")
    if hydrate:
        snapshots.clear_snapshot_cache(include_disk=False)
    installed = snapshots.get_tool_snapshot("ruff", cache_root=roots[0])
    assert installed.available is True
    assert installed.binary_path == str(binaries[0])
    assert installed.version == (None if local_command == "versionless" else "101.0.0")
    assert snapshots.get_tool_snapshot("ruff", cache_root=roots[1]) == initial[1]

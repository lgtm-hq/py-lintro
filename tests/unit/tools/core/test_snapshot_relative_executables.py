"""Relative executable probes and cache keys follow the execution directory."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from lintro.plugins.protocol import ToolDefinition
from lintro.tools.core import snapshots

_COMMAND = "./bin/custom-linter"


@pytest.fixture(params=["explicit", "relative-path", "empty-path"])
def executable_layout(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[str, str]:
    """Select the command and its location relative to the execution root."""
    if request.param == "explicit":
        return _COMMAND, _COMMAND
    path_entry = "./bin" if request.param == "relative-path" else ""
    monkeypatch.setenv("PATH", path_entry)
    return "custom-linter", _COMMAND if path_entry else "custom-linter"


@pytest.fixture(params=["versionless", "versioned", "manifest"])
def local_command(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    executable_layout: tuple[str, str],
) -> str:
    """Provide relative plugin and manifest commands, retaining real subprocesses."""
    mode = request.param
    executable, _ = executable_layout
    definition = ToolDefinition(
        "ruff",
        "project-local linter",
        version_command=None if mode == "versionless" else [executable, "--version"],
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
        lambda name: SimpleNamespace(version_command=[executable, "--version"]),
    )
    monkeypatch.setattr(
        snapshots.CommandBuilderRegistry,
        "get_command",
        lambda *args: [executable],
    )
    return mode


def _write_linter(root: Path, version: str, relative_path: str) -> Path:
    """Create a real executable with deterministic version output."""
    binary = root / relative_path
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_text(f"#!/bin/sh\necho 'ruff {version}'\n", encoding="utf-8")
    binary.chmod(0o755)
    return binary


@pytest.mark.parametrize("use_search_root", [False, True], ids=["cwd", "search-root"])
def test_relative_executable_probe_uses_execution_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    local_command: str,
    executable_layout: tuple[str, str],
    use_search_root: bool,
) -> None:
    """A tool outside process cwd has the same availability and binary identity."""
    root = tmp_path / "project"
    binary = _write_linter(root, "99.0.0", executable_layout[1])
    monkeypatch.chdir(tmp_path if use_search_root else root)

    result = (
        snapshots.probe_tool("ruff", search_root=root)
        if use_search_root
        else snapshots.probe_tool("ruff")
    )

    assert result.available is True
    assert result.version_check_passed is True
    assert result.binary_path == str(binary)
    assert result.binary_mtime == binary.stat().st_mtime
    assert result.version == (None if local_command == "versionless" else "99.0.0")


def test_relative_executable_cache_freshness_uses_execution_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    local_command: str,
    executable_layout: tuple[str, str],
) -> None:
    """Correct cached evidence stays fresh even when process cwd has another tool."""
    root = tmp_path / "project"
    executable, relative_path = executable_layout
    binary = _write_linter(root, "99.0.0", relative_path)
    _write_linter(tmp_path, "1.0.0", relative_path)
    monkeypatch.chdir(tmp_path)
    command = (
        (executable, "--version") if local_command == "manifest" else (executable,)
    )
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
    executable_layout: tuple[str, str],
) -> None:
    """Hits, upgrades, removal and installation are isolated by execution root."""
    roots = [tmp_path / "first", tmp_path / "second"]
    versions = ["99.0.0", "98.0.0"]
    relative_path = executable_layout[1]
    binaries = [
        _write_linter(r, v, relative_path) for r, v in zip(roots, versions, strict=True)
    ]
    _write_linter(tmp_path, "1.0.0", relative_path)
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

    _write_linter(roots[0], "100.0.0", relative_path)
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
    _write_linter(roots[0], "101.0.0", relative_path)
    if hydrate:
        snapshots.clear_snapshot_cache(include_disk=False)
    installed = snapshots.get_tool_snapshot("ruff", cache_root=roots[0])
    assert installed.available is True
    assert installed.binary_path == str(binaries[0])
    assert installed.version == (None if local_command == "versionless" else "101.0.0")
    assert snapshots.get_tool_snapshot("ruff", cache_root=roots[1]) == initial[1]


@pytest.mark.parametrize("executable_layout", ["relative-path"], indirect=True)
@pytest.mark.parametrize("hydrate", [False, True], ids=["memory", "disk"])
def test_path_order_change_invalidates_cached_binary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    local_command: str,
    hydrate: bool,
) -> None:
    """Absolute entries retain their priority when PATH selects a different tool."""
    root = tmp_path / "project"
    relative = _write_linter(root, "99.0.0", _COMMAND)
    absolute = _write_linter(tmp_path / "shared", "98.0.0", _COMMAND)
    _write_linter(tmp_path, "1.0.0", _COMMAND)
    monkeypatch.chdir(tmp_path)

    for entries, binary, version in [
        (["./bin", str(absolute.parent)], relative, "99.0.0"),
        ([str(absolute.parent), "./bin"], absolute, "98.0.0"),
        (["./bin", str(absolute.parent)], relative, "99.0.0"),
    ]:
        monkeypatch.setenv("PATH", os.pathsep.join(entries))
        if hydrate:
            snapshots.clear_snapshot_cache(include_disk=False)
        result = snapshots.get_tool_snapshot("ruff", cache_root=root)
        assert result.available is True
        assert result.binary_path == str(binary)
        assert result.binary_mtime == binary.stat().st_mtime
        assert result.version == (None if local_command == "versionless" else version)

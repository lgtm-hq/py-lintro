"""Regression coverage for PR 1485's cache and availability review findings."""

from __future__ import annotations

import json
import shutil
import subprocess  # nosec B404 - version-probe failure fixtures replace subprocess.run
import sys
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from lintro import __version__
from lintro.plugins.execution_preparation import verify_tool_version
from lintro.plugins.protocol import ToolDefinition
from lintro.tools.core import snapshots
from lintro.tools.core.version_parsing import check_tool_version


def _binary_snapshot(root: Path, name: str, version: str) -> snapshots.ToolSnapshot:
    """Construct evidence tied to a stable temporary binary."""
    binary = root / name
    if not binary.exists():
        binary.write_text("executable")
        binary.chmod(0o755)
    return snapshots.ToolSnapshot(
        name=name,
        available=True,
        version=version,
        version_check_passed=True,
        binary_path=str(binary),
        binary_mtime=binary.stat().st_mtime,
        command=(str(binary),),
    )


@pytest.mark.parametrize("hydrate", [False, True], ids=["memory", "disk"])
def test_subset_lookup_preserves_peers_and_original_expiry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    hydrate: bool,
) -> None:
    """Subset and partial-coverage lookups probe only entries whose TTL expired."""
    clock = [1000.0]
    counts = {"ruff": 0, "black": 0}

    def probe(name: str, *, search_root: Path) -> snapshots.ToolSnapshot:
        counts[name] += 1
        return _binary_snapshot(search_root, name, str(counts[name]))

    monkeypatch.setattr(time, "time", lambda: clock[0])
    monkeypatch.setattr(snapshots, "get_snapshot_ttl", lambda: 10)
    monkeypatch.setattr(snapshots, "probe_tool", probe)
    monkeypatch.setattr(
        snapshots,
        "get_executable_command",
        lambda name, **kw: [str(tmp_path / name)],
    )
    snapshots.get_tool_snapshot("ruff", cache_root=tmp_path)
    clock[0] += 5
    snapshots.get_tool_snapshot("black", cache_root=tmp_path)
    if hydrate:
        snapshots.clear_snapshot_cache(include_disk=False)
    clock[0] += 4
    both = snapshots.probe_all_tools(tool_names=["ruff", "black"], cache_root=tmp_path)
    assert counts == {"ruff": 1, "black": 1}
    assert both["ruff"].probed_at == 1000
    assert both["black"].probed_at == 1005
    clock[0] += 2
    expired = snapshots.probe_all_tools(
        tool_names=["ruff", "black"],
        cache_root=tmp_path,
    )
    assert counts == {"ruff": 2, "black": 1}
    assert expired["ruff"].version == "2"
    assert expired["black"].probed_at == 1005


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("available", "false"),
        ("version_check_passed", 1),
        ("below_recommended", []),
        ("version", {}),
        ("probe_error", 42),
        ("remediation_hint", False),
        ("command", "ruff"),
        ("command", [7]),
        ("binary_path", []),
        ("binary_mtime", float("nan")),
        ("binary_mtime", 10**1000),
        ("probed_at", True),
        ("probed_at", float("inf")),
        ("name", None),
    ],
)
def test_malformed_snapshot_field_reprobes_instead_of_claiming_availability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
) -> None:
    """Valid JSON with invalid field types cannot be a trusted cache hit."""
    entry = snapshots.ToolSnapshot(
        name="ruff",
        available=True,
        version="stale",
    ).to_dict()
    entry[field] = value
    with pytest.raises(ValueError):
        snapshots.ToolSnapshot.from_dict(entry)
    path = tmp_path / snapshots.SNAPSHOT_CACHE_RELPATH
    path.parent.mkdir()
    path.write_text(
        json.dumps({"lintro_version": __version__, "snapshots": {"ruff": entry}}),
    )
    monkeypatch.setattr(
        snapshots,
        "probe_tool",
        lambda *a, **kw: snapshots.ToolSnapshot(name="ruff", probe_error="missing"),
    )
    result = snapshots.get_tool_snapshot("ruff", cache_root=tmp_path)
    assert result.available is False
    assert result.probe_error == "missing"


@pytest.mark.parametrize("present", [False, True])
@pytest.mark.parametrize("in_process", [None, False])
def test_ungated_external_plugin_requires_an_executable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    present: bool,
    in_process: bool | None,
) -> None:
    """No version metadata does not imply in-process execution."""
    definition = ToolDefinition("ungated", "external plugin", in_process=in_process)
    monkeypatch.setattr(
        "lintro.tools.tool_manager.is_tool_registered",
        lambda name: True,
    )
    binary = tmp_path / "ungated"
    if present:
        binary.write_text("tool")
        binary.chmod(0o755)
    monkeypatch.setattr(
        "lintro.tools.tool_manager.get_tool",
        lambda name: SimpleNamespace(definition=definition),
    )
    monkeypatch.setattr(
        snapshots,
        "get_executable_command",
        lambda *a, **kw: [str(binary)],
    )
    if not present and in_process is None:
        with pytest.raises(ValueError, match="in_process=True"):
            snapshots.probe_tool("ungated", search_root=tmp_path)
        return
    result = snapshots.probe_tool("ungated", search_root=tmp_path)
    assert result.available is present
    assert result.version_check_passed is present
    assert result.command == (str(binary),)


@pytest.mark.parametrize("hydrate", [False, True], ids=["memory", "disk"])
def test_old_unavailable_cache_cannot_hide_plugin_migration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    hydrate: bool,
) -> None:
    """Legacy unavailable entries fail closed, then recover after declaration."""
    plugin = SimpleNamespace(
        definition=ToolDefinition("ungated", "plugin", in_process=False),
    )
    monkeypatch.setattr(
        "lintro.tools.tool_manager.is_tool_registered",
        lambda name: True,
    )
    monkeypatch.setattr("lintro.tools.tool_manager.get_tool", lambda name: plugin)
    monkeypatch.setattr(
        snapshots,
        "get_executable_command",
        lambda *a, **kw: [str(tmp_path / "missing")],
    )
    missing = snapshots.get_tool_snapshot("ungated", cache_root=tmp_path)
    assert missing.available is False
    plugin.definition = replace(plugin.definition, in_process=None)
    if hydrate:
        snapshots.clear_snapshot_cache(include_disk=False)
    with pytest.raises(ValueError, match="execution mode is unspecified"):
        verify_tool_version(plugin.definition, cwd=tmp_path)
    plugin.definition = replace(plugin.definition, in_process=True)
    assert verify_tool_version(plugin.definition, cwd=tmp_path) is None
    assert snapshots.get_tool_snapshot("ungated", cache_root=tmp_path).available is True


def test_missing_cached_command_requires_fresh_binary_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An incomplete disk entry cannot bypass command identity checks."""
    entry = _binary_snapshot(tmp_path, "ruff", "stale").to_dict()
    del entry["command"]
    cache = tmp_path / snapshots.SNAPSHOT_CACHE_RELPATH
    cache.parent.mkdir()
    cache.write_text(
        json.dumps({"lintro_version": __version__, "snapshots": {"ruff": entry}}),
    )
    monkeypatch.setattr(
        snapshots,
        "probe_tool",
        lambda *a, **kw: snapshots.ToolSnapshot(name="ruff", probe_error="missing"),
    )
    fresh = snapshots.get_tool_snapshot("ruff", cache_root=tmp_path)
    assert fresh.available is False
    assert fresh.probe_error == "missing"


@pytest.mark.parametrize(
    "error",
    [AttributeError("broken builder"), ImportError("broken plugin")],
)
def test_unexpected_probe_error_reaches_execution_failure_handling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    """Internal bugs must never become non-strict successful unavailability."""

    def crash(*args: object, **kwargs: object) -> snapshots.ToolSnapshot:
        raise error

    monkeypatch.setattr(snapshots, "probe_tool", crash)
    with pytest.raises(type(error), match=str(error)):
        verify_tool_version(ToolDefinition("ruff", "lint"), cwd=tmp_path)
    assert not (tmp_path / snapshots.SNAPSHOT_CACHE_RELPATH).exists()


@pytest.mark.parametrize(
    "command",
    [
        ["bunx", "prettier"],
        [sys.executable, "-m", "ghost_module"],
        ["/opt/other/python", "-m", "ghost_module"],
    ],
)
def test_wrapper_without_package_identity_reprobes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: list[str],
) -> None:
    """An unchanged wrapper cannot keep a stale package version alive."""
    versions = iter(["1.0", "2.0"])
    monkeypatch.setattr(snapshots, "get_executable_command", lambda *a, **kw: command)
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name, **kw: None if kw else command[0],
    )
    monkeypatch.setattr(
        snapshots,
        "probe_tool",
        lambda name, **kw: snapshots.ToolSnapshot(
            name=name,
            available=True,
            version=next(versions),
            command=tuple(command),
        ),
    )
    first = snapshots.get_tool_snapshot("ruff", cache_root=tmp_path)
    second = snapshots.get_tool_snapshot("ruff", cache_root=tmp_path)
    assert (first.version, second.version) == ("1.0", "2.0")


@pytest.mark.parametrize("hydrate", [False, True], ids=["memory", "disk"])
def test_transient_probe_failure_is_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    hydrate: bool,
) -> None:
    """A present executable gets another chance in this or the next process."""
    calls: list[str] = []
    binary = tmp_path / "ruff"
    binary.write_text("x")
    binary.chmod(0o755)
    monkeypatch.setattr(
        snapshots,
        "get_executable_command",
        lambda *a, **kw: [str(binary)],
    )

    def probe(name: str, **kwargs: object) -> snapshots.ToolSnapshot:
        calls.append(name)
        return snapshots.ToolSnapshot(
            name=name,
            available=len(calls) > 1,
            version="2.0" if len(calls) > 1 else None,
            binary_path=str(binary),
            binary_mtime=binary.stat().st_mtime,
            command=(str(binary),),
            probe_error="timeout" if len(calls) == 1 else None,
        )

    monkeypatch.setattr(snapshots, "probe_tool", probe)
    first = snapshots.get_tool_snapshot("ruff", cache_root=tmp_path)
    if hydrate:
        snapshots.clear_snapshot_cache(include_disk=False)
    recovered = snapshots.get_tool_snapshot("ruff", cache_root=tmp_path)
    cached = snapshots.get_tool_snapshot("ruff", cache_root=tmp_path)
    assert first.available is False
    assert recovered.available is True
    assert recovered.version == "2.0"
    assert cached == recovered
    assert calls == ["ruff", "ruff"]


@pytest.mark.parametrize("hydrate", [False, True], ids=["memory", "disk"])
@pytest.mark.parametrize(
    "command",
    [
        ["cargo", "audit"],
        ["cargo", "deny"],
        ["rustup", "run", "stable", "rustfmt"],
        ["rustc"],
        ["rustfmt"],
        ["cargo-clippy"],
        ["custom-rust-proxy"],
    ],
)
def test_rust_wrapper_changes_invalidate_unchanged_executable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    hydrate: bool,
    command: list[str],
) -> None:
    """Package upgrades and removal cannot hide behind a stable Rust launcher."""
    wrapper = tmp_path / command[0]
    if command[0] == "custom-rust-proxy":
        rustup = tmp_path / "rustup"
        rustup.write_text("stable proxy")
        rustup.chmod(0o755)
        wrapper.symlink_to(rustup)
    else:
        wrapper.write_text("stable launcher")
        wrapper.chmod(0o755)
    resolved = [str(wrapper), *command[1:]]
    original_mtime = wrapper.stat().st_mtime
    versions = iter(["1.0", "2.0", None])

    def probe(name: str, **kwargs: object) -> snapshots.ToolSnapshot:
        version = next(versions)
        return snapshots.ToolSnapshot(
            name=name,
            available=version is not None,
            version=version,
            version_check_passed=version is not None,
            binary_path=str(wrapper),
            binary_mtime=original_mtime,
            command=tuple(resolved),
            probe_error=None if version else "toolchain component removed",
        )

    monkeypatch.setattr(snapshots, "get_executable_command", lambda *a, **kw: resolved)
    monkeypatch.setattr(snapshots, "probe_tool", probe)
    first = snapshots.get_tool_snapshot("rustfmt", cache_root=tmp_path)
    if hydrate:
        snapshots.clear_snapshot_cache(include_disk=False)
    upgraded = snapshots.get_tool_snapshot("rustfmt", cache_root=tmp_path)
    if hydrate:
        snapshots.clear_snapshot_cache(include_disk=False)
    removed = snapshots.get_tool_snapshot("rustfmt", cache_root=tmp_path)
    assert (first.version, upgraded.version) == ("1.0", "2.0")
    assert removed.available is False
    assert removed.probe_error == "toolchain component removed"
    assert wrapper.stat().st_mtime == original_mtime


@pytest.mark.parametrize("hydrate", [False, True], ids=["memory", "disk"])
@pytest.mark.parametrize("first_name", ["example-tool", "example_tool"])
def test_force_refresh_replaces_every_cached_alias(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    hydrate: bool,
    first_name: str,
) -> None:
    """Refreshing either spelling replaces the identity seen by both spellings."""
    other_name = "example_tool" if first_name == "example-tool" else "example-tool"
    versions = iter(["old", "new"])
    binary = tmp_path / "tool-bin"
    monkeypatch.setattr(
        snapshots,
        "get_executable_command",
        lambda *a, **kw: [str(binary)],
    )
    monkeypatch.setattr(
        snapshots,
        "probe_tool",
        lambda name, **kw: replace(
            _binary_snapshot(tmp_path, "tool-bin", next(versions)),
            name=name,
        ),
    )
    old = snapshots.get_tool_snapshot(first_name, cache_root=tmp_path)
    refreshed = snapshots.get_tool_snapshot(other_name, force=True, cache_root=tmp_path)
    if hydrate:
        snapshots.clear_snapshot_cache(include_disk=False)
    reread = snapshots.get_tool_snapshot(first_name, cache_root=tmp_path)
    assert old.version == "old"
    assert reread == refreshed
    assert reread.version == "new"
    disk = json.loads((tmp_path / snapshots.SNAPSHOT_CACHE_RELPATH).read_text())
    assert list(disk["snapshots"]) == [other_name]


def test_validated_disk_entry_wins_over_expired_memory_during_subset_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A peer process's refresh survives merging another newly probed tool."""
    clock = [1000.0]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    monkeypatch.setattr(snapshots, "get_snapshot_ttl", lambda: 10)
    monkeypatch.setattr(
        snapshots,
        "get_executable_command",
        lambda name, **kw: [str(tmp_path / name)],
    )
    monkeypatch.setattr(
        snapshots,
        "probe_tool",
        lambda name, *, search_root: _binary_snapshot(search_root, name, "old"),
    )
    old = snapshots.get_tool_snapshot("ruff", cache_root=tmp_path)
    clock[0] += 11
    disk_refresh = replace(old, version="from-peer-process", probed_at=clock[0])
    snapshots._write_disk_cache(
        tmp_path / snapshots.SNAPSHOT_CACHE_RELPATH,
        {"ruff": disk_refresh},
        ttl=10,
    )
    both = snapshots.probe_all_tools(tool_names=["ruff", "black"], cache_root=tmp_path)
    assert both["ruff"] == disk_refresh
    assert both["black"].probed_at == clock[0]
    assert snapshots.get_tool_snapshot("ruff", cache_root=tmp_path) == disk_refresh


def test_subset_merge_preserves_peer_published_during_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Discarding rejected candidates must still preserve concurrent updates."""
    calls: list[str] = []

    def probe(name: str, *, search_root: Path) -> snapshots.ToolSnapshot:
        calls.append(name)
        if name == "black":
            snapshots.get_tool_snapshot("ruff", cache_root=search_root)
        return _binary_snapshot(search_root, name, "fresh")

    monkeypatch.setattr(snapshots, "probe_tool", probe)
    monkeypatch.setattr(
        snapshots,
        "get_executable_command",
        lambda name, **kw: [str(tmp_path / name)],
    )
    black = snapshots.get_tool_snapshot("black", cache_root=tmp_path)
    ruff = snapshots.get_tool_snapshot("ruff", cache_root=tmp_path)
    assert (black.version, ruff.version) == ("fresh", "fresh")
    assert calls == ["black", "ruff"]


def test_subset_merge_cannot_resurrect_alias_replaced_during_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A concurrent refresh invalidates aliases retained before probing began."""
    calls: list[str] = []

    def probe(name: str, *, search_root: Path) -> snapshots.ToolSnapshot:
        calls.append(name)
        if name == "black":
            snapshots.get_tool_snapshot(
                "example_tool",
                force=True,
                cache_root=search_root,
            )
        version = "old" if name == "example-tool" else "new"
        return replace(_binary_snapshot(search_root, "tool-bin", version), name=name)

    monkeypatch.setattr(snapshots, "probe_tool", probe)
    monkeypatch.setattr(
        snapshots,
        "get_executable_command",
        lambda name, **kw: [str(tmp_path / "tool-bin")],
    )
    old = snapshots.get_tool_snapshot("example-tool", cache_root=tmp_path)
    snapshots.get_tool_snapshot("black", cache_root=tmp_path)
    refreshed = snapshots.get_tool_snapshot("example-tool", cache_root=tmp_path)
    assert old.version == "old"
    assert refreshed.version == "new"
    assert calls == ["example-tool", "black", "example_tool"]


def test_transient_cache_read_error_does_not_delete_valid_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A read failure provides no evidence that an optional cache is corrupt."""
    cache_file = tmp_path / snapshots.SNAPSHOT_CACHE_RELPATH
    snapshots._write_disk_cache(
        cache_file,
        {"ruff": _binary_snapshot(tmp_path, "ruff", "1.0")},
        ttl=600,
    )
    original = cache_file.read_bytes()

    def fail(*args: object, **kwargs: object) -> str:
        raise OSError("temporarily unreadable")

    monkeypatch.setattr(Path, "read_text", fail)
    loaded = snapshots._load_disk_cache(cache_file, ttl=600, search_root=tmp_path)
    assert loaded == {}
    assert cache_file.exists()
    assert cache_file.read_bytes() == original


def test_no_cache_bypasses_nested_project_disk_after_another_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A root lookup cannot consume --no-cache before nested execution starts."""
    nested = tmp_path / "nested"
    nested.mkdir()
    monkeypatch.setattr(
        snapshots,
        "get_executable_command",
        lambda name, *, cwd: [str(cwd / name)],
    )
    version = ["old"]
    monkeypatch.setattr(
        snapshots,
        "probe_tool",
        lambda name, *, search_root: _binary_snapshot(search_root, name, version[0]),
    )
    snapshots.get_tool_snapshot("ruff", cache_root=nested)
    snapshots.clear_snapshot_cache(include_disk=False)
    version[0] = "new"
    snapshots.set_force_fresh_probes(True)
    root_fresh = snapshots.get_tool_snapshot("black", cache_root=tmp_path)
    refreshed = snapshots.get_tool_snapshot("ruff", cache_root=nested)
    assert refreshed.version == "new"
    root_hit = snapshots.get_tool_snapshot("black", cache_root=tmp_path)
    assert root_hit.version == "new"
    assert root_hit == root_fresh


@pytest.mark.parametrize("operation", ["read", "replace"])
def test_disk_io_failure_keeps_fresh_probe_and_cleans_tempfiles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    """An optional cache's read/write failure cannot hide a healthy tool."""

    def fail(*args: object, **kwargs: object) -> None:
        raise OSError("transient disk failure")

    monkeypatch.setattr(Path, "read_text" if operation == "read" else "replace", fail)
    monkeypatch.setattr(
        snapshots,
        "probe_tool",
        lambda name, **kw: snapshots.ToolSnapshot(
            name=name,
            available=True,
            version="fresh",
        ),
    )
    result = snapshots.get_tool_snapshot("ruff", cache_root=tmp_path)
    assert result.version == "fresh"
    assert list((tmp_path / ".lintro-cache").glob("*.tmp")) == []


@pytest.mark.parametrize(
    "reader",
    [snapshots.is_strict_missing_tools, snapshots.get_snapshot_ttl],
)
def test_config_load_failure_does_not_disable_strict_policy(
    monkeypatch: pytest.MonkeyPatch,
    reader: Callable[[], object],
) -> None:
    """Malformed configuration propagates rather than substituting defaults."""

    def fail(*args: object, **kwargs: object) -> None:
        raise ValueError("invalid execution config")

    monkeypatch.setattr("lintro.config.config_loader.get_config", fail)
    with pytest.raises(ValueError, match="invalid execution config"):
        reader()


@pytest.mark.parametrize("failure", ["timeout", "nonzero", "parse"])
def test_version_probe_classification_is_structured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    """Only parse failures on resolved binaries can proceed without a version."""

    def run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        if failure == "timeout":
            raise subprocess.TimeoutExpired("ruff", 1)
        return subprocess.CompletedProcess(
            ["ruff"],
            1 if failure == "nonzero" else 0,
            "not a version",
            "",
        )

    monkeypatch.setattr(subprocess, "run", run)
    info = check_tool_version("ruff", ["ruff"])
    # Prose changes cannot affect the availability decision.
    info.error_message = "arbitrary diagnostic"
    result = snapshots._snapshot_from_version(
        _binary_snapshot(tmp_path, "ruff", "1.0"),
        info,
        "install",
    )
    assert info.probe_failed is (failure != "parse")
    assert info.parse_failed is (failure == "parse")
    assert result.available is (failure == "parse")


def test_registered_plugin_construction_error_is_not_a_missing_tool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A plugin's ValueError must escape rather than enter the manifest fallback."""

    def construct(name: str) -> object:
        raise ValueError("plugin initialization failed")

    monkeypatch.setattr(
        "lintro.tools.tool_manager.is_tool_registered",
        lambda name: True,
    )
    monkeypatch.setattr("lintro.tools.tool_manager.get_tool", construct)
    with pytest.raises(ValueError, match="plugin initialization failed"):
        snapshots.get_tool_snapshot("ruff", cache_root=tmp_path)

"""Regressions for capability snapshots across projects and watch batches."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.plugins.execution_preparation import verify_tool_version
from lintro.plugins.registry import ToolRegistry
from lintro.tools.core import snapshots


@pytest.fixture
def snapshot_clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Control cache age without waiting or changing filesystem granularity.

    Args:
        monkeypatch: Scoped environment and function overrides.

    Returns:
        Mutable clock used by the snapshot layer.
    """
    clock = [1000.0]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    monkeypatch.setattr(snapshots, "get_snapshot_ttl", lambda: 10)
    return clock


@pytest.fixture
def local_prettier(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Resolve a temporary executable through the real Node command builder.

    Args:
        tmp_path: Isolated project directory.
        monkeypatch: Scoped command resolution override.

    Returns:
        Project root with a version-reporting binary, without installing packages.
    """
    from lintro.tools.core import command_builders

    project = tmp_path / "project"
    project.mkdir()
    binary = project / "prettier"
    binary.write_text('#!/bin/sh\nprintf "99.0.0\\n"\n')
    binary.chmod(0o755)
    monkeypatch.setattr(
        command_builders,
        "find_local_node_binary",
        lambda name, *, start=None: (
            str(Path(start) / name) if start and (Path(start) / name).exists() else None
        ),
    )
    return project


def test_version_gate_and_config_probe_use_target_project(
    local_prettier: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A target's working binary and config win over the caller's broken binary.

    Args:
        local_prettier: Target project with a real executable.
        tmp_path: Caller directory.
        monkeypatch: Scoped working-directory override.
    """
    (tmp_path / "prettier").write_text("#!/bin/sh\nexit 1\n")
    (tmp_path / "prettier").chmod(0o755)
    (local_prettier / ".prettierrc").write_text("{}")
    monkeypatch.chdir(tmp_path)
    definition = ToolRegistry.get("prettier").definition

    verdict = verify_tool_version(definition, cwd=local_prettier)
    snapshot = snapshots.get_tool_snapshot("prettier", cache_root=local_prettier)

    assert_that(verdict).is_none()
    assert_that(snapshot.version).is_equal_to("99.0.0")
    assert_that(snapshot.binary_path).is_equal_to(str(local_prettier / "prettier"))
    assert_that(snapshot.capabilities.config_found).is_true()
    assert_that(snapshot.capabilities.supports_stdin).is_true()


def test_new_local_binary_replaces_still_existing_cached_binary(
    local_prettier: Path,
) -> None:
    """Resolution changes invalidate a cache even while the old binary survives.

    Args:
        local_prettier: Isolated project with an executable.
    """
    nested = local_prettier / "nested"
    nested.mkdir()
    binary_parent = local_prettier / "prettier"
    from unittest.mock import patch

    # Keep the old binary live while resolution starts preferring a new one.
    with patch(
        "lintro.tools.core.command_builders.shutil.which",
        return_value=str(binary_parent),
    ):
        first = snapshots.get_tool_snapshot("prettier", cache_root=nested)
    binary = nested / "prettier"
    binary.write_text('#!/bin/sh\nprintf "100.0.0\\n"\n')
    binary.chmod(0o755)
    second = snapshots.get_tool_snapshot("prettier", cache_root=nested)

    assert_that(first.binary_path).is_not_equal_to(second.binary_path)
    assert_that(second.binary_path).is_equal_to(str(binary))
    assert_that(second.version).is_equal_to("100.0.0")


def test_subset_refresh_does_not_renew_older_tool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    snapshot_clock: list[float],
) -> None:
    """A watch batch for black cannot extend an older ruff snapshot's TTL.

    Args:
        tmp_path: Isolated cache directory.
        monkeypatch: Scoped probe override.
        snapshot_clock: Controlled snapshot clock.
    """
    versions = {"ruff": "1.0", "black": "2.0"}

    def probe(name: str, *, search_root: Path) -> snapshots.ToolSnapshot:
        return snapshots.ToolSnapshot(
            name=name,
            available=True,
            version=versions[name],
            version_check_passed=True,
        )

    monkeypatch.setattr(snapshots, "probe_tool", probe)
    first = snapshots.get_tool_snapshot("ruff", cache_root=tmp_path)
    snapshot_clock[0] += 9
    snapshots.get_tool_snapshot("black", cache_root=tmp_path)
    versions["ruff"] = "3.0"
    snapshot_clock[0] += 2
    refreshed = snapshots.get_tool_snapshot("ruff", cache_root=tmp_path)

    assert_that(first.version).is_equal_to("1.0")
    assert_that(refreshed.version).is_equal_to("3.0")


def test_disk_hydration_does_not_restart_ttl(
    local_prettier: Path,
    snapshot_clock: list[float],
) -> None:
    """Loading a nine-second-old disk entry leaves only one second of its TTL.

    Args:
        local_prettier: Project with a real version-reporting executable.
        snapshot_clock: Controlled snapshot clock.
    """
    snapshots.get_tool_snapshot("prettier", cache_root=local_prettier)
    snapshots.clear_snapshot_cache(include_disk=False)
    snapshot_clock[0] += 9
    hydrated = snapshots.get_tool_snapshot("prettier", cache_root=local_prettier)
    binary = local_prettier / "prettier"
    original = binary.stat()
    binary.write_text('#!/bin/sh\nprintf "101.0.0\\n"\n')
    os.utime(binary, ns=(original.st_atime_ns, original.st_mtime_ns))
    snapshot_clock[0] += 2
    expired = snapshots.get_tool_snapshot("prettier", cache_root=local_prettier)

    assert_that(hydrated.version).is_equal_to("99.0.0")
    assert_that(expired.version).is_equal_to("101.0.0")


@pytest.mark.parametrize("capabilities", [[1], True, {"supports_stdin": "true"}])
def test_malformed_capabilities_in_valid_json_trigger_reprobe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capabilities: object,
) -> None:
    """A structurally corrupt cache cannot claim a tool is available.

    Args:
        tmp_path: Isolated cache directory.
        monkeypatch: Scoped probe override.
        capabilities: Invalid capability payload inside otherwise valid JSON.
    """
    from lintro import __version__

    cached = snapshots.ToolSnapshot(
        name="ruff",
        available=True,
        version="stale",
    ).to_dict()
    cached["capabilities"] = capabilities
    cache_file = tmp_path / snapshots.SNAPSHOT_CACHE_RELPATH
    cache_file.parent.mkdir()
    cache_file.write_text(
        json.dumps(
            {
                "lintro_version": __version__,
                "probed_at": time.time(),
                "snapshots": {"ruff": cached},
            },
        ),
    )
    fresh = snapshots.ToolSnapshot(name="ruff", available=True, version="fresh")
    monkeypatch.setattr(snapshots, "probe_tool", lambda *args, **kwargs: fresh)

    result = snapshots.get_tool_snapshot("ruff", cache_root=tmp_path)

    assert_that(result.version).is_equal_to("fresh")
    assert_that(
        json.loads(cache_file.read_text())["snapshots"]["ruff"]["version"],
    ).is_equal_to("fresh")


def test_python_module_upgrade_invalidates_without_interpreter_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Python module probes track the tool script rather than only Python's mtime.

    Args:
        tmp_path: Isolated scripts and cache directory.
        monkeypatch: Scoped version probe and script-directory overrides.
    """
    from lintro.tools.core.version_parsing import ToolVersionInfo

    binary = tmp_path / "ruff"
    binary.write_text("1.0.0")
    binary.chmod(0o755)
    import sysconfig

    monkeypatch.setattr(sysconfig, "get_path", lambda name: str(tmp_path))
    monkeypatch.setattr(
        snapshots,
        "get_executable_command",
        lambda *args, **kwargs: [sys.executable, "-m", "ruff"],
    )
    monkeypatch.setattr(
        "lintro.tools.core.version_parsing.check_tool_version",
        lambda *args, **kwargs: ToolVersionInfo(
            name="ruff",
            current_version=binary.read_text(),
            version_check_passed=True,
        ),
    )
    first = snapshots.get_tool_snapshot("ruff", cache_root=tmp_path)
    binary.write_text("2.0.0")
    os.utime(binary, (first.binary_mtime + 100, first.binary_mtime + 100))
    second = snapshots.get_tool_snapshot("ruff", cache_root=tmp_path)

    assert_that(first.binary_path).is_equal_to(str(binary))
    assert_that(first.version).is_equal_to("1.0.0")
    assert_that(second.version).is_equal_to("2.0.0")

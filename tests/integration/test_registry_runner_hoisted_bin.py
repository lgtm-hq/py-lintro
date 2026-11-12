"""Real npx/bunx against a workspace that hoists an older binary (#2043).

Each test first runs the real runner, fully offline, to pin down how it
picks the executable. It then checks that lintro's version gate reports the
binary that runner would launch. The registry is unreachable and every cache
is empty, so a test that ever needed the network would fail rather than
download.

npx is gated with a plain skip, not ``require_command``: the tools image ships
bun (with ``node`` linked to it) but no npm, and ``require_command`` turns an
absent command into a failure there (#465). bunx is in the image, so it uses
``require_command``.
"""

from __future__ import annotations

import os
import shutil
import subprocess  # nosec B404 - runs real npx/bunx with fixed argv, offline
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro._tool_versions import get_tool_version
from lintro.tools.core import snapshots
from tests.integration._tools import NO_MIN_VERSION, require_command

_PIN = get_tool_version("prettier") or ""
_HOISTED = "1.0.0"
_UNREACHABLE_REGISTRY = "http://127.0.0.1:9"


def _fake_prettier(package_dir: Path, version: str) -> None:
    """Install a fake ``prettier`` whose node binary names its own version.

    Args:
        package_dir: ``node_modules/prettier`` directory to create.
        version: Version the package and binary report.
    """
    package_dir.mkdir(parents=True)
    (package_dir / "package.json").write_text(
        f'{{"name":"prettier","version":"{version}",' '"bin":{"prettier":"bin.js"}}\n',
        encoding="utf-8",
    )
    binary = package_dir / "bin.js"
    binary.write_text(
        f'#!/usr/bin/env node\nconsole.log("{version}")\n',
        encoding="utf-8",
    )
    binary.chmod(0o755)


@pytest.fixture
def hoisted_workspace(tmp_path: Path) -> Path:
    """Workspace whose root hoists an older prettier while the tree has the pin.

    Args:
        tmp_path: Temporary directory provided by pytest.

    Returns:
        The workspace package directory (no ``node_modules/.bin`` of its own).
    """
    workspace = tmp_path / "ws"
    (workspace / ".git").mkdir(parents=True)
    (workspace / "package.json").write_text(
        '{"name":"ws","private":true,"workspaces":["packages/*"]}\n',
        encoding="utf-8",
    )
    app = workspace / "packages" / "app"
    app.mkdir(parents=True)
    (app / "package.json").write_text(
        '{"name":"app","version":"1.0.0"}\n',
        encoding="utf-8",
    )
    _fake_prettier(workspace / "node_modules" / "prettier", _HOISTED)
    other = workspace / "node_modules" / "other"
    other.mkdir(parents=True)
    (other / "package.json").write_text(
        f'{{"name":"other","version":"1.0.0","dependencies":{{"prettier":"{_PIN}"}}}}\n',
        encoding="utf-8",
    )
    _fake_prettier(other / "node_modules" / "prettier", _PIN)
    bin_dir = workspace / "node_modules" / ".bin"
    bin_dir.mkdir()
    (bin_dir / "prettier").symlink_to("../prettier/bin.js")
    return app


def _isolate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    commands: dict[str, str],
) -> dict[str, str]:
    """Expose only *commands* on PATH, offline, with empty runner caches.

    Args:
        monkeypatch: Scoped environment override.
        tmp_path: Directory for the PATH shim and caches.
        commands: Command name -> real executable to link into PATH.

    Returns:
        The environment the real runner should use.
    """
    shim = tmp_path / "path"
    shim.mkdir()
    for name, target in commands.items():
        (shim / name).symlink_to(target)
    monkeypatch.setenv("PATH", f"{shim}{os.pathsep}/bin")
    monkeypatch.setenv("npm_config_registry", _UNREACHABLE_REGISTRY)
    monkeypatch.setenv("npm_config_cache", str(tmp_path / "npm-cache"))
    monkeypatch.setenv("npm_config_fetch_retries", "0")
    monkeypatch.setenv("npm_config_update_notifier", "false")
    monkeypatch.setenv("BUN_CONFIG_REGISTRY", _UNREACHABLE_REGISTRY)
    monkeypatch.setenv("BUN_INSTALL_CACHE_DIR", str(tmp_path / "bun-cache"))
    return dict(os.environ)


def _run(argv: list[str], cwd: Path, env: dict[str, str]) -> str:
    """Run a real runner and return its combined output.

    Args:
        argv: Command to run.
        cwd: Working directory.
        env: Environment from :func:`_isolate`.

    Returns:
        stdout followed by stderr.
    """
    result = subprocess.run(  # nosec B603 - fixed argv, shell=False
        argv,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    return result.stdout + result.stderr


_NPX = shutil.which("npx")
_NODE = shutil.which("node")


@pytest.mark.skipif(
    _NPX is None or _NODE is None,
    reason="npx and node are required; the tools image ships bun only",
)
def test_npx_launches_the_hoisted_binary_and_the_gate_refuses_it(
    hoisted_workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Npx skips the pinned install and runs the hoisted copy; lintro gates it.

    Args:
        hoisted_workspace: Workspace package directory.
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Scoped environment override.
    """
    assert _NPX is not None and _NODE is not None
    env = _isolate(monkeypatch, tmp_path, {"npx": _NPX, "node": _NODE})

    launched = _run(
        ["npx", "--yes", "--package", f"prettier@{_PIN}", "prettier"],
        hoisted_workspace,
        env,
    )
    snapshot = snapshots.probe_tool("prettier", search_root=hoisted_workspace)

    assert_that(launched.strip().splitlines()[-1]).is_equal_to(_HOISTED)
    assert_that(snapshot.command[0]).ends_with("node_modules/.bin/prettier")
    assert_that(snapshot.version).is_equal_to(_HOISTED)
    assert_that(snapshot.version_check_passed).is_false()
    assert_that(snapshot.probe_error).contains("below minimum requirement")


@require_command("bunx", ["bunx"], min_version=NO_MIN_VERSION)
def test_bunx_ignores_the_hoisted_binary_and_the_gate_keeps_the_pin(
    hoisted_workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bunx goes to the registry for its pin and never runs the local tree.

    Args:
        hoisted_workspace: Workspace package directory.
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Scoped environment override.
    """
    bunx = shutil.which("bunx")
    assert bunx is not None
    env = _isolate(monkeypatch, tmp_path, {"bunx": bunx})

    launched = _run(["bunx", f"prettier@{_PIN}"], hoisted_workspace, env)
    snapshot = snapshots.probe_tool("prettier", search_root=hoisted_workspace)

    assert_that(launched).does_not_contain(_HOISTED)
    assert_that(launched).does_not_contain(f"{_PIN}\n")
    assert_that(snapshot.command[0]).is_equal_to("bunx")
    assert_that(snapshot.available).is_true()
    assert_that(snapshot.version).is_equal_to(_PIN)

"""Real npx/bunx: which binary they launch, and whether lintro's gate agrees.

``npx --package <pkg>@<pin> <bin>`` skips its install only when the pin is
already installed somewhere in npm's project tree, then runs ``<bin>`` by name
from an ancestor ``node_modules/.bin``, or exits 127. Without the pin
installed it runs the pin (#2043). Each npx test first runs real npx to show
which binary it launches, then checks lintro's snapshot for the same layout.

Nothing reaches the internet. A localhost registry serves a fake pinned
``prettier`` whose binary prints ``registry <pin>``, so "npx ran the pin" is
observed rather than inferred. Caches start empty.

npx is gated with a plain skip, not ``require_command``: the tools image ships
bun (with ``node`` linked to it) but no npm, and ``require_command`` turns an
absent command into a failure there (#465). bunx is in the image, so it uses
``require_command``.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess  # nosec B404 - runs real npx/bunx with fixed argv, offline
import tarfile
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro._tool_versions import get_tool_version
from lintro.tools.core import snapshots
from tests.integration._tools import NO_MIN_VERSION, require_command

_PIN = get_tool_version("prettier") or ""
_OLDER = "1.0.0"
_UNREACHABLE_REGISTRY = "http://127.0.0.1:9"
_NPX = shutil.which("npx")
_NODE = shutil.which("node")

requires_npx = pytest.mark.skipif(
    _NPX is None or _NODE is None,
    reason="npx and node are required; the tools image ships bun only",
)


def _pinned_tarball() -> bytes:
    """Build the npm tarball the localhost registry serves for the pin.

    Returns:
        Gzipped tar bytes with ``package/package.json`` and ``package/bin.js``.
    """
    files = {
        "package/package.json": (
            json.dumps(
                {"name": "prettier", "version": _PIN, "bin": {"prettier": "bin.js"}},
            ).encode(),
            0o644,
        ),
        "package/bin.js": (
            f'#!/usr/bin/env node\nconsole.log("registry {_PIN}")\n'.encode(),
            0o755,
        ),
    }
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, (data, mode) in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = mode
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


@pytest.fixture(scope="module")
def registry() -> Iterator[str]:
    """Serve the pinned fake ``prettier`` from localhost.

    Yields:
        str: The registry URL.
    """
    tarball = _pinned_tarball()
    ports: list[int] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            port = ports[0]
            if self.path.rstrip("/") == "/prettier":
                body = json.dumps(
                    {
                        "name": "prettier",
                        "dist-tags": {"latest": _PIN},
                        "versions": {
                            _PIN: {
                                "name": "prettier",
                                "version": _PIN,
                                "bin": {"prettier": "bin.js"},
                                "dist": {
                                    "tarball": (
                                        f"http://127.0.0.1:{port}/prettier-{_PIN}.tgz"
                                    ),
                                },
                            },
                        },
                    },
                ).encode()
            elif self.path.endswith(".tgz"):
                body = tarball
            else:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    ports.append(server.server_port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/"
    server.shutdown()
    thread.join()


def _package(directory: Path, version: str, label: str) -> Path:
    """Install a fake ``prettier`` whose node binary prints *label* and version.

    Args:
        directory: Package directory to create.
        version: Version the package reports.
        label: Word printed before the version, to identify the copy.

    Returns:
        The package's executable.
    """
    directory.mkdir(parents=True)
    (directory / "package.json").write_text(
        json.dumps(
            {"name": "prettier", "version": version, "bin": {"prettier": "bin.js"}},
        ),
        encoding="utf-8",
    )
    binary = directory / "bin.js"
    binary.write_text(
        f'#!/usr/bin/env node\nconsole.log("{label} {version}")\n',
        encoding="utf-8",
    )
    binary.chmod(0o755)
    return binary


def _link_bin(bin_dir: Path, target: str) -> None:
    """Link ``<bin_dir>/prettier`` to *target*, as npm does.

    Args:
        bin_dir: ``node_modules/.bin`` directory.
        target: Relative link target.
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    (bin_dir / "prettier").symlink_to(target)


def _workspace(root: Path) -> Path:
    """Create a git workspace with one member and no member ``node_modules``.

    Args:
        root: Workspace root.

    Returns:
        The member directory.
    """
    (root / ".git").mkdir(parents=True)
    (root / "package.json").write_text(
        '{"name":"ws","private":true,"workspaces":["packages/*"]}\n',
        encoding="utf-8",
    )
    app = root / "packages" / "app"
    app.mkdir(parents=True)
    (app / "package.json").write_text(
        '{"name":"app","version":"1.0.0"}\n',
        encoding="utf-8",
    )
    return app


def _layout(tmp_path: Path, name: str) -> Path:
    """Build one of the four review layouts.

    Args:
        tmp_path: Directory to build in.
        name: Layout identifier.

    Returns:
        The directory npx and lintro run in.
    """
    if name == "outside-bin-no-pin":
        home = tmp_path / "home"
        _package(home / "node_modules" / "prettier", _OLDER, "outside")
        _link_bin(home / "node_modules" / ".bin", "../prettier/bin.js")
        project = home / "proj"
        (project / ".git").mkdir(parents=True)
        (project / "package.json").write_text(
            '{"name":"proj","version":"1.0.0"}\n',
            encoding="utf-8",
        )
        return project
    root = tmp_path / "ws"
    app = _workspace(root)
    modules = root / "node_modules"
    if name == "nested-pin-no-bin":
        wrapper = modules / "wrapper"
        wrapper.mkdir(parents=True)
        (wrapper / "package.json").write_text(
            json.dumps(
                {
                    "name": "wrapper",
                    "version": "1.0.0",
                    "dependencies": {"prettier": _PIN},
                },
            ),
            encoding="utf-8",
        )
        _package(wrapper / "node_modules" / "prettier", _PIN, "nested")
        _link_bin(wrapper / "node_modules" / ".bin", "../prettier/bin.js")
        return app
    _package(modules / "prettier", _OLDER, "hoisted")
    _link_bin(modules / ".bin", "../prettier/bin.js")
    if name == "hoisted-older-pin-elsewhere":
        _package(modules / "other" / "node_modules" / "prettier", _PIN, "nested")
    return app


def _isolate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    commands: dict[str, str],
    registry_url: str,
) -> dict[str, str]:
    """Expose only *commands* on PATH, with a private registry and caches.

    Args:
        monkeypatch: Scoped environment override.
        tmp_path: Directory for the PATH shim, HOME and caches.
        commands: Command name -> real executable to link into PATH.
        registry_url: Registry both runners use.

    Returns:
        The environment the real runner should use.
    """
    shim = tmp_path / "path"
    shim.mkdir()
    for name, target in commands.items():
        (shim / name).symlink_to(target)
    home = tmp_path / "fake-home"
    home.mkdir()
    monkeypatch.setenv("PATH", f"{shim}{os.pathsep}/bin")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("npm_config_registry", registry_url)
    monkeypatch.setenv("npm_config_cache", str(tmp_path / "npm-cache"))
    monkeypatch.setenv("npm_config_update_notifier", "false")
    monkeypatch.setenv("npm_config_audit", "false")
    monkeypatch.setenv("npm_config_fund", "false")
    monkeypatch.setenv("BUN_CONFIG_REGISTRY", _UNREACHABLE_REGISTRY)
    monkeypatch.setenv("BUN_INSTALL_CACHE_DIR", str(tmp_path / "bun-cache"))
    return dict(os.environ)


def _run(argv: list[str], cwd: Path, env: dict[str, str]) -> tuple[int, str]:
    """Run a real runner.

    Args:
        argv: Command to run.
        cwd: Working directory.
        env: Environment from :func:`_isolate`.

    Returns:
        Exit code and the last line of combined output.
    """
    result = subprocess.run(  # nosec B603 - fixed argv, shell=False
        argv,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    lines = (result.stdout + result.stderr).strip().splitlines()
    return result.returncode, lines[-1] if lines else ""


def _npx_then_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    registry_url: str,
    layout: str,
) -> tuple[int, str, snapshots.ToolSnapshot]:
    """Run real npx in *layout*, then lintro's probe for the same directory.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Scoped environment override.
        registry_url: Localhost registry URL.
        layout: Layout identifier.

    Returns:
        npx's exit code, its last output line, and lintro's snapshot.
    """
    assert _NPX is not None and _NODE is not None
    cwd = _layout(tmp_path, layout)
    env = _isolate(monkeypatch, tmp_path, {"npx": _NPX, "node": _NODE}, registry_url)
    code, launched = _run(
        ["npx", "--yes", "--package", f"prettier@{_PIN}", "prettier"],
        cwd,
        env,
    )
    return code, launched, snapshots.probe_tool("prettier", search_root=cwd)


@requires_npx
def test_npx_runs_hoisted_older_when_pin_is_elsewhere_and_gate_refuses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    registry: str,
) -> None:
    """Pin installed elsewhere: npx runs the hoisted copy; lintro refuses it.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Scoped environment override.
        registry: Localhost registry URL.
    """
    code, launched, snapshot = _npx_then_probe(
        tmp_path,
        monkeypatch,
        registry,
        "hoisted-older-pin-elsewhere",
    )

    assert_that(code).is_zero()
    assert_that(launched).is_equal_to(f"hoisted {_OLDER}")
    assert_that(snapshot.command[0]).ends_with("node_modules/.bin/prettier")
    assert_that(snapshot.version).is_equal_to(_OLDER)
    assert_that(snapshot.version_check_passed).is_false()
    assert_that(snapshot.probe_error).contains("below minimum requirement")


@requires_npx
@pytest.mark.parametrize("layout", ["hoisted-older-no-pin", "outside-bin-no-pin"])
def test_npx_runs_the_pin_without_one_installed_and_gate_keeps_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    registry: str,
    layout: str,
) -> None:
    """No pin installed: npx installs and runs it; local bins are ignored.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Scoped environment override.
        registry: Localhost registry URL.
        layout: Layout identifier.
    """
    code, launched, snapshot = _npx_then_probe(tmp_path, monkeypatch, registry, layout)

    assert_that(code).is_zero()
    assert_that(launched).is_equal_to(f"registry {_PIN}")
    assert_that(snapshot.command[0]).is_equal_to("npx")
    assert_that(snapshot.available).is_true()
    assert_that(snapshot.version).is_equal_to(_PIN)
    assert_that(snapshot.probe_error).is_none()


@requires_npx
def test_npx_exits_127_with_pin_but_no_reachable_bin_and_gate_refuses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    registry: str,
) -> None:
    """Pin installed but no ``.bin`` on npx's PATH: npx fails; lintro refuses.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Scoped environment override.
        registry: Localhost registry URL.
    """
    code, launched, snapshot = _npx_then_probe(
        tmp_path,
        monkeypatch,
        registry,
        "nested-pin-no-bin",
    )

    assert_that(code).is_equal_to(127)
    assert_that(launched).contains("not found")
    assert_that(snapshot.available).is_false()
    assert_that(snapshot.version).is_none()
    assert_that(snapshot.probe_error).contains("but no prettier on its PATH")


@require_command("bunx", ["bunx"], min_version=NO_MIN_VERSION)
def test_bunx_ignores_the_hoisted_binary_and_the_gate_keeps_the_pin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With bunx, the registry pin runs and the local tree is never used.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Scoped environment override.
    """
    bunx = shutil.which("bunx")
    assert bunx is not None
    cwd = _layout(tmp_path, "hoisted-older-pin-elsewhere")
    env = _isolate(monkeypatch, tmp_path, {"bunx": bunx}, _UNREACHABLE_REGISTRY)

    _code, launched = _run(["bunx", f"prettier@{_PIN}"], cwd, env)
    snapshot = snapshots.probe_tool("prettier", search_root=cwd)

    assert_that(launched).does_not_contain("hoisted")
    assert_that(launched).does_not_contain("nested")
    assert_that(snapshot.command[0]).is_equal_to("bunx")
    assert_that(snapshot.available).is_true()
    assert_that(snapshot.version).is_equal_to(_PIN)

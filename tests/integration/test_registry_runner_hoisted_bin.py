"""Real npx/bunx: what they launch, and lintro never passing on a pin they skip.

``npx --package <pkg>@<pin> <bin>`` may reuse anything npm finds installed and
run ``<bin>`` by name from an ancestor ``node_modules/.bin``, so its pin is not
reliable evidence once a ``node_modules`` tree is in reach (#2043). lintro
therefore trusts an npx pin only when no ``node_modules`` exists in the
execution directory or above it and npm bin links are not disabled; in every
other npx-only case the tool is unavailable. ``bunx <pkg>@<pin>`` always
resolves from the registry, so its pin stands.

Each npx test first runs real npx to show what it launches, then checks that
lintro either refuses or reports exactly what npx ran. A pin is never reported
available while npx runs something else.

Nothing reaches the internet. A localhost registry serves a fake pinned
``prettier`` whose binary prints ``registry <pin>``. Caches start empty.

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
import time
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro._tool_versions import get_tool_version
from lintro.models.core.tool_snapshot import ToolSnapshot
from lintro.tools.core import snapshots
from tests.integration._tools import NO_MIN_VERSION, require_command

_PIN = get_tool_version("prettier") or ""
_OLDER = "1.0.0"
_UNREACHABLE_REGISTRY = "http://127.0.0.1:9"
_NPX = shutil.which("npx")
_NODE = shutil.which("node")
_REFUSAL = "npx may run an unpinned copy"

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


# ---------------------------------------------------------------------------
# Layout builders
# ---------------------------------------------------------------------------


def _package(
    directory: Path,
    version: str,
    *,
    label: str | None = None,
    name: str = "prettier",
    dependencies: dict[str, str] | None = None,
) -> None:
    """Write a fake package, optionally with a node binary printing *label*.

    Args:
        directory: Package directory to create.
        version: Version the package reports.
        label: Text the binary prints, or None for a package without one.
        name: Package name.
        dependencies: Declared dependencies.
    """
    directory.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, object] = {"name": name, "version": version}
    if dependencies:
        manifest["dependencies"] = dependencies
    if label is not None:
        manifest["bin"] = {name: "bin.js"}
        binary = directory / "bin.js"
        binary.write_text(
            f'#!/usr/bin/env node\nconsole.log("{label}")\n',
            encoding="utf-8",
        )
        binary.chmod(0o755)
    (directory / "package.json").write_text(json.dumps(manifest), encoding="utf-8")


def _hoist_older(root: Path) -> None:
    """Hoist an older ``prettier`` with its ``.bin`` link at *root*.

    Args:
        root: Directory owning the ``node_modules``.
    """
    _package(root / "node_modules" / "prettier", _OLDER, label=f"hoisted {_OLDER}")
    bin_dir = root / "node_modules" / ".bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    (bin_dir / "prettier").symlink_to("../prettier/bin.js")


def _workspace(root: Path, pattern: str = "packages/*") -> Path:
    """Create a git workspace with members ``app`` and ``lib``.

    Args:
        root: Workspace root.
        pattern: The root's ``workspaces`` glob.

    Returns:
        The ``app`` member, which has no ``node_modules`` of its own.
    """
    (root / ".git").mkdir(parents=True)
    (root / "package.json").write_text(
        json.dumps({"name": "ws", "private": True, "workspaces": [pattern]}),
        encoding="utf-8",
    )
    _package(root / "packages" / "lib", "1.0.0", name="lib")
    app = root / "packages" / "app"
    _package(app, "1.0.0", name="app")
    return app


def _hoisted_with_pin_elsewhere(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    app = _workspace(root)
    _hoist_older(root)
    _package(root / "node_modules" / "other" / "node_modules" / "prettier", _PIN)
    return app


def _hoisted_no_pin(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    app = _workspace(root)
    _hoist_older(root)
    return app


def _nested_pin_no_bin(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    app = _workspace(root)
    wrapper = root / "node_modules" / "wrapper"
    _package(wrapper, "1.0.0", name="wrapper", dependencies={"prettier": _PIN})
    _package(wrapper / "node_modules" / "prettier", _PIN, label=f"nested {_PIN}")
    bin_dir = wrapper / "node_modules" / ".bin"
    bin_dir.mkdir()
    (bin_dir / "prettier").symlink_to("../prettier/bin.js")
    return app


def _outside_bin_no_pin(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    _hoist_older(home)
    project = home / "proj"
    (project / ".git").mkdir(parents=True)
    _package(project, "1.0.0", name="proj")
    return project


def _pnpm_transitive_pin(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    app = _workspace(root)
    _hoist_older(root)
    store = root / "node_modules" / ".pnpm"
    _package(store / f"prettier@{_PIN}" / "node_modules" / "prettier", _PIN)
    middle = store / "middle@1.0.0" / "node_modules"
    _package(middle / "middle", "1.0.0", name="middle", dependencies={"prettier": _PIN})
    (middle / "prettier").symlink_to(f"../../prettier@{_PIN}/node_modules/prettier")
    wrapper = store / "wrapper@1.0.0" / "node_modules"
    _package(
        wrapper / "wrapper",
        "1.0.0",
        name="wrapper",
        dependencies={"middle": "1.0.0"},
    )
    (wrapper / "middle").symlink_to("../../middle@1.0.0/node_modules/middle")
    (root / "node_modules" / "wrapper").symlink_to(
        ".pnpm/wrapper@1.0.0/node_modules/wrapper",
    )
    return app


def _stale_hidden_lockfile(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    app = _workspace(root)
    _hoist_older(root)
    other = root / "node_modules" / "other"
    _package(other, "1.0.0", name="other", dependencies={"prettier": _PIN})
    _package(other / "node_modules" / "prettier", _OLDER)
    lockfile = root / "node_modules" / ".package-lock.json"
    lockfile.write_text(
        json.dumps(
            {
                "name": "ws",
                "lockfileVersion": 3,
                "requires": True,
                "packages": {
                    "node_modules/prettier": {
                        "version": _OLDER,
                        "bin": {"prettier": "bin.js"},
                    },
                    "node_modules/other": {
                        "version": "1.0.0",
                        "dependencies": {"prettier": _PIN},
                    },
                    "node_modules/other/node_modules/prettier": {"version": _PIN},
                },
            },
        ),
        encoding="utf-8",
    )
    # npm trusts a hidden lockfile only when it is newer than every folder.
    newer = time.time() + 60
    os.utime(lockfile, (newer, newer))
    return app


def _brace_glob(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    app = _workspace(root, "packages/{app,lib}")
    _hoist_older(root)
    _package(root / "node_modules" / "other" / "node_modules" / "prettier", _PIN)
    return app


def _extglob(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    app = _workspace(root, "packages/@(app|lib)")
    _hoist_older(root)
    _package(root / "node_modules" / "other" / "node_modules" / "prettier", _PIN)
    return app


def _clean_project(tmp_path: Path) -> Path:
    project = tmp_path / "proj"
    (project / ".git").mkdir(parents=True)
    _package(project, "1.0.0", name="proj")
    return project


# ---------------------------------------------------------------------------
# Runner helpers
# ---------------------------------------------------------------------------


def _isolate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    commands: dict[str, str],
    registry_url: str,
    extra_env: dict[str, str],
) -> dict[str, str]:
    """Expose only *commands* on PATH, with a private registry and caches.

    Args:
        monkeypatch: Scoped environment override.
        tmp_path: Directory for the PATH shim, HOME and caches.
        commands: Command name -> real executable to link into PATH.
        registry_url: Registry both runners use.
        extra_env: Extra variables for the layout, such as bin links.

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
    for key, value in extra_env.items():
        monkeypatch.setenv(key, value)
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
    build: Callable[[Path], Path],
    extra_env: dict[str, str] | None = None,
) -> tuple[int, str, ToolSnapshot]:
    """Run real npx in a layout, then lintro's probe for the same directory.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Scoped environment override.
        registry_url: Localhost registry URL.
        build: Layout builder returning the execution directory.
        extra_env: Extra variables for both npx and lintro.

    Returns:
        npx's exit code, its last output line, and lintro's snapshot.
    """
    assert _NPX is not None and _NODE is not None
    cwd = build(tmp_path)
    env = _isolate(
        monkeypatch,
        tmp_path,
        {"npx": _NPX, "node": _NODE},
        registry_url,
        extra_env or {},
    )
    code, launched = _run(
        ["npx", "--yes", "--package", f"prettier@{_PIN}", "prettier"],
        cwd,
        env,
    )
    return code, launched, snapshots.probe_tool("prettier", search_root=cwd)


def _assert_never_a_false_pass(snapshot: ToolSnapshot, launched: str) -> None:
    """Require lintro to refuse, or to report exactly the binary npx ran.

    Args:
        snapshot: lintro's probe result.
        launched: What real npx printed.
    """
    if snapshot.available and snapshot.version_check_passed:
        assert_that(snapshot.command[0]).is_equal_to("npx")
        assert_that(launched).is_equal_to(f"registry {snapshot.version}")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


_REFUSED_LAYOUTS: list[tuple[str, Callable[[Path], Path], dict[str, str], str]] = [
    (
        "hoisted-older-pin-elsewhere",
        _hoisted_with_pin_elsewhere,
        {},
        f"hoisted {_OLDER}",
    ),
    ("hoisted-older-no-pin", _hoisted_no_pin, {}, f"registry {_PIN}"),
    ("nested-pin-no-bin", _nested_pin_no_bin, {}, "not found"),
    ("outside-bin-no-pin", _outside_bin_no_pin, {}, f"registry {_PIN}"),
    ("pnpm-transitive-pin", _pnpm_transitive_pin, {}, f"hoisted {_OLDER}"),
    ("stale-hidden-lockfile", _stale_hidden_lockfile, {}, f"hoisted {_OLDER}"),
    (
        "bin-links-off-hoisted",
        _hoisted_no_pin,
        {"NPM_CONFIG_BIN_LINKS": "false"},
        f"hoisted {_OLDER}",
    ),
    ("workspace-brace-glob", _brace_glob, {}, f"hoisted {_OLDER}"),
    ("workspace-extglob", _extglob, {}, f"hoisted {_OLDER}"),
]


@requires_npx
@pytest.mark.parametrize(
    ("build", "extra_env", "npx_ran"),
    [(build, env, ran) for _name, build, env, ran in _REFUSED_LAYOUTS],
    ids=[name for name, _build, _env, _ran in _REFUSED_LAYOUTS],
)
def test_npx_only_with_a_node_modules_tree_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    registry: str,
    build: Callable[[Path], Path],
    extra_env: dict[str, str],
    npx_ran: str,
) -> None:
    """Wherever npx could reuse something, lintro refuses instead of trusting the pin.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Scoped environment override.
        registry: Localhost registry URL.
        build: Layout builder.
        extra_env: Extra variables for the layout.
        npx_ran: What real npx prints in this layout.
    """
    _code, launched, snapshot = _npx_then_probe(
        tmp_path,
        monkeypatch,
        registry,
        build,
        extra_env,
    )

    assert_that(launched).contains(npx_ran)
    assert_that(snapshot.available).is_false()
    assert_that(snapshot.version).is_none()
    assert_that(snapshot.probe_error).contains(_REFUSAL)
    _assert_never_a_false_pass(snapshot, launched)


def _has_node_modules_above(directory: Path) -> bool:
    """Report whether a ``node_modules`` exists in *directory* or an ancestor.

    Args:
        directory: Directory to start from.

    Returns:
        True when the clean-project precondition cannot hold on this machine.
    """
    origin = directory.resolve()
    return any((d / "node_modules").exists() for d in (origin, *origin.parents))


@requires_npx
def test_npx_only_in_a_clean_project_trusts_the_pin_npx_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    registry: str,
) -> None:
    """With nothing to reuse, npx runs the pin and lintro reports exactly that.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Scoped environment override.
        registry: Localhost registry URL.
    """
    if _has_node_modules_above(tmp_path):
        pytest.skip("a node_modules directory exists above the temporary directory")

    code, launched, snapshot = _npx_then_probe(
        tmp_path,
        monkeypatch,
        registry,
        _clean_project,
    )

    assert_that(code).is_zero()
    assert_that(launched).is_equal_to(f"registry {_PIN}")
    assert_that(snapshot.command[0]).is_equal_to("npx")
    assert_that(snapshot.available).is_true()
    assert_that(snapshot.version).is_equal_to(_PIN)
    _assert_never_a_false_pass(snapshot, launched)


@requires_npx
def test_npx_only_with_bin_links_off_is_refused_even_when_clean(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    registry: str,
) -> None:
    """Bin links off: npx installs the pin but cannot run it; lintro refuses.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Scoped environment override.
        registry: Localhost registry URL.
    """
    if _has_node_modules_above(tmp_path):
        pytest.skip("a node_modules directory exists above the temporary directory")

    code, launched, snapshot = _npx_then_probe(
        tmp_path,
        monkeypatch,
        registry,
        _clean_project,
        {"NPM_CONFIG_BIN_LINKS": "false"},
    )

    assert_that(code).is_equal_to(127)
    assert_that(launched).contains("not found")
    assert_that(snapshot.available).is_false()
    assert_that(snapshot.probe_error).contains("bin links are disabled")
    _assert_never_a_false_pass(snapshot, launched)


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
    cwd = _hoisted_with_pin_elsewhere(tmp_path)
    env = _isolate(monkeypatch, tmp_path, {"bunx": bunx}, _UNREACHABLE_REGISTRY, {})

    _code, launched = _run(["bunx", f"prettier@{_PIN}"], cwd, env)
    snapshot = snapshots.probe_tool("prettier", search_root=cwd)

    assert_that(launched).does_not_contain("hoisted")
    assert_that(launched).does_not_contain("nested")
    assert_that(snapshot.command[0]).is_equal_to("bunx")
    assert_that(snapshot.available).is_true()
    assert_that(snapshot.version).is_equal_to(_PIN)

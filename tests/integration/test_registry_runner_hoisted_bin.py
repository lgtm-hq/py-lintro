"""Real npx/bunx: what they launch, and lintro never passing on a pin they skip.

``npx --package <pkg>@<pin> <bin>`` may reuse anything npm finds installed and
run ``<bin>`` by name from an ancestor ``node_modules/.bin``, so its pin is
not reliable evidence once npm has a tree to reuse (#2043). lintro's npx
command carries flags that neutralise npm's workspace, bin-link, global and
offline settings, and its own ``--cache``. lintro refuses an npx-only
fallback when a ``node_modules`` directory or a ``package.json`` declaring
workspaces (or naming the package) sits in the execution directory or above
it, or when ``npm_config_workspace`` is set in the environment. Anything else
set in an npmrc file is left to npm: the flags win, or npm fails loudly and
lintro reports an execution failure. ``bunx <pkg>@<pin>`` always resolves from
the registry, so its pin stands.

Each npx test probes with lintro and then runs the exact command lintro
generated. A shared check fails if lintro reports a pin as available while
that command runs something else, unless npm failed loudly and lintro
classifies that failure as an execution failure.

Nothing reaches the internet. A localhost registry serves a fake pinned
``prettier`` whose binary prints ``registry <pin>``, to npm and bun alike.
Caches, lintro's npx cache and ``HOME`` are private to each test.

npx is gated with a plain skip, not ``require_command``: the tools image ships
bun (with ``node`` linked to it) but no npm, and ``require_command`` turns an
absent command into a failure there (#465). bun is in the image, so the bunx
test uses ``require_command``.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import subprocess  # nosec B404 - runs real npx/bunx with fixed argv, offline
import sys
import tarfile
import threading
import time
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro._tool_versions import get_tool_version
from lintro.models.core.tool_outcome import Checked, ToolOutcome
from lintro.models.core.tool_result import ToolResult
from lintro.models.core.tool_snapshot import ToolSnapshot
from lintro.tools.core import snapshots
from lintro.tools.core.npx_cache import npx_cache_dir
from lintro.utils import file_cache
from lintro.utils.execution.result_shaping import classify_runner_failure
from tests.integration._tools import NO_MIN_VERSION, require_command

_PIN = get_tool_version("prettier") or ""
_OLDER = "1.0.0"
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
    """Expose only *commands* on PATH, with a private registry, HOME and caches.

    Args:
        monkeypatch: Scoped environment override.
        tmp_path: Directory for the PATH shim, HOME and caches.
        commands: Command name -> real executable to link into PATH.
        registry_url: Registry npm and bun both use.
        extra_env: Extra variables for the layout, such as npm config.

    Returns:
        The environment the real runner should use.
    """
    shim = tmp_path / "path"
    shim.mkdir()
    for name, target in commands.items():
        (shim / name).symlink_to(target)
    home = tmp_path / "fake-home"
    home.mkdir(exist_ok=True)
    # lintro's own npx cache, never the developer's real ``~/.lintro``.
    monkeypatch.setattr(file_cache, "CACHE_DIR", tmp_path / "lintro-cache")
    monkeypatch.setenv("PATH", f"{shim}{os.pathsep}/bin")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("npm_config_registry", registry_url)
    monkeypatch.setenv("npm_config_cache", str(tmp_path / "npm-cache"))
    monkeypatch.setenv("npm_config_update_notifier", "false")
    monkeypatch.setenv("npm_config_audit", "false")
    monkeypatch.setenv("npm_config_fund", "false")
    monkeypatch.setenv("BUN_CONFIG_REGISTRY", registry_url)
    monkeypatch.setenv("BUN_INSTALL_CACHE_DIR", str(tmp_path / "bun-cache"))
    for key, value in extra_env.items():
        monkeypatch.setenv(key, value)
    return dict(os.environ)


def _run(argv: list[str], cwd: Path, env: dict[str, str]) -> tuple[int, str]:
    """Run a real runner and report what the launched tool printed.

    Runners write install progress to stderr and the tool writes to stdout, so
    the last stdout line names what ran; without stdout, the last stderr line
    carries the failure (``command not found``, ``npm error``).

    Args:
        argv: Command to run.
        cwd: Working directory.
        env: Environment from :func:`_isolate`.

    Returns:
        Exit code and the last stdout line, else the last stderr line.
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
    lines = result.stdout.strip().splitlines() or result.stderr.strip().splitlines()
    return result.returncode, lines[-1] if lines else ""


def _probe_then_npx(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    registry_url: str,
    build: Callable[[Path], Path],
    configure: Callable[[Path, Path], dict[str, str]] | None = None,
) -> tuple[int, str, ToolSnapshot]:
    """Probe with lintro, then run the exact npx command lintro generated.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Scoped environment override.
        registry_url: Localhost registry URL.
        build: Layout builder returning the execution directory.
        configure: Writes npm config for the layout and returns extra env.

    Returns:
        npx's exit code, what it launched, and lintro's snapshot.
    """
    assert _NPX is not None and _NODE is not None
    cwd = build(tmp_path)
    extra = configure(tmp_path, cwd) if configure is not None else {}
    env = _isolate(
        monkeypatch,
        tmp_path,
        {"npx": _NPX, "node": _NODE},
        registry_url,
        extra,
    )
    snapshot = snapshots.probe_tool("prettier", search_root=cwd)
    assert_that(snapshot.command[0]).is_equal_to("npx")
    code, launched = _run(list(snapshot.command), cwd, env)
    return code, launched, snapshot


def _assert_never_a_false_pass(
    snapshot: ToolSnapshot,
    launched: str,
    code: int,
    output: str | None = None,
) -> None:
    """Require lintro to refuse, report exactly what npx ran, or fail loudly.

    A passing gate is acceptable only when the generated command ran the pin,
    or when npm itself failed and lintro reports that run as an execution
    failure rather than as findings or a pass.

    Args:
        snapshot: lintro's probe result.
        launched: Last line the command lintro generated printed.
        code: That command's exit status.
        output: Its full output, when the run failed.
    """
    if not (snapshot.available and snapshot.version_check_passed):
        return
    assert_that(snapshot.command[0]).is_equal_to("npx")
    if code == 0:
        assert_that(launched).is_equal_to(f"registry {snapshot.version}")
        return
    failed = ToolResult.from_outcome(
        ToolOutcome(execution=Checked(success=False)),
        name="prettier",
        output=output if output is not None else launched,
    )
    classified = classify_runner_failure(failed, command=snapshot.command)
    assert_that(classified.execution_failed).is_true()
    assert_that(classified.findings_complete).is_false()


def _require_clean_ancestry(tmp_path: Path) -> None:
    """Skip when a ``node_modules`` or ``package.json`` sits above *tmp_path*.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    origin = tmp_path.resolve()
    for directory in (origin, *origin.parents):
        if (directory / "node_modules").exists() or (
            directory / "package.json"
        ).exists():
            pytest.skip(f"{directory} holds npm project files above the test directory")


# ---------------------------------------------------------------------------
# Workspace layouts and npm config sources
# ---------------------------------------------------------------------------


def _member_tree(tmp_path: Path, *, nested_pin: bool) -> Path:
    """Workspace root without ``node_modules``; the member has its own tree.

    The member ``packages/app`` holds prettier 1.0.0 with its ``.bin`` link
    and, when *nested_pin*, a transitive copy at the pin.

    Args:
        tmp_path: Directory to build in.
        nested_pin: Whether the member's tree also holds the pin.

    Returns:
        The workspace root, where lintro and npx run.
    """
    root = tmp_path / "ws"
    app = _workspace(root)
    _hoist_older(app)
    if nested_pin:
        wrapper = app / "node_modules" / "wrapper"
        _package(wrapper, "1.0.0", name="wrapper", dependencies={"prettier": _PIN})
        _package(wrapper / "node_modules" / "prettier", _PIN)
    return root


def _at_root(build: Callable[[Path], Path]) -> Callable[[Path], Path]:
    """Run a workspace-member layout from its workspace root instead.

    With ``--workspaces=false`` npm stops at the member's own ``package.json``
    and never loads the root tree, so these variants keep the root tree in
    play.

    Args:
        build: Builder returning ``<root>/packages/app``.

    Returns:
        A builder returning ``<root>``.
    """
    return lambda tmp_path: build(tmp_path).parent.parent


def _member_tree_with_pin(tmp_path: Path) -> Path:
    return _member_tree(tmp_path, nested_pin=True)


def _member_tree_without_pin(tmp_path: Path) -> Path:
    return _member_tree(tmp_path, nested_pin=False)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _env(**values: str) -> Callable[[Path, Path], dict[str, str]]:
    """Config source: environment variables only.

    Args:
        **values: Variables to set.

    Returns:
        A configure callable for :func:`_probe_then_npx`.
    """
    return lambda _tmp, _cwd: dict(values)


def _project_rc(text: str) -> Callable[[Path, Path], dict[str, str]]:
    """Config source: ``.npmrc`` in the execution directory.

    Args:
        text: File content.

    Returns:
        A configure callable for :func:`_probe_then_npx`.
    """

    def configure(_tmp: Path, cwd: Path) -> dict[str, str]:
        _write(cwd / ".npmrc", text)
        return {}

    return configure


def _user_rc(text: str) -> Callable[[Path, Path], dict[str, str]]:
    """Config source: ``$HOME/.npmrc``.

    Args:
        text: File content.

    Returns:
        A configure callable for :func:`_probe_then_npx`.
    """

    def configure(tmp: Path, _cwd: Path) -> dict[str, str]:
        _write(tmp / "fake-home" / ".npmrc", text)
        return {}

    return configure


def _userconfig_rc(text: str) -> Callable[[Path, Path], dict[str, str]]:
    """Config source: a file named by ``npm_config_userconfig``.

    Args:
        text: File content.

    Returns:
        A configure callable for :func:`_probe_then_npx`.
    """

    def configure(tmp: Path, _cwd: Path) -> dict[str, str]:
        _write(tmp / "custom-userrc", text)
        return {"npm_config_userconfig": str(tmp / "custom-userrc")}

    return configure


def _globalconfig_rc(text: str) -> Callable[[Path, Path], dict[str, str]]:
    """Config source: a file named by ``npm_config_globalconfig``.

    Args:
        text: File content.

    Returns:
        A configure callable for :func:`_probe_then_npx`.
    """

    def configure(tmp: Path, _cwd: Path) -> dict[str, str]:
        _write(tmp / "globalrc", text)
        return {"npm_config_globalconfig": str(tmp / "globalrc")}

    return configure


def _global_prefix_with_older_bin(tmp: Path, _cwd: Path) -> dict[str, str]:
    """Config source: a global prefix holding the pin behind an older ``bin``.

    Args:
        tmp: Temporary directory.
        _cwd: Execution directory (unused).

    Returns:
        Extra environment pointing npm at the prefix.
    """
    prefix = tmp / "global-prefix"
    _package(prefix / "lib" / "node_modules" / "prettier", _PIN)
    _package(
        prefix / "lib" / "node_modules" / "old",
        _OLDER,
        name="old",
        label=f"global {_OLDER}",
    )
    (prefix / "bin").mkdir(parents=True)
    (prefix / "bin" / "prettier").symlink_to("../lib/node_modules/old/bin.js")
    return {"npm_config_prefix": str(prefix)}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

_Configure = Callable[[Path, Path], dict[str, str]] | None

#: (id, layout, config, what lintro's generated npx command launches, reason).
#: From a workspace member, ``--workspaces=false`` keeps npm on the member's
#: own tree, so npx often runs the pin there; lintro still refuses, because a
#: ``node_modules`` it cannot vouch for sits above. From the root, npx still
#: fails with every flag set (``-at-root``, ``member-tree-*``). A root with its
#: own ``node_modules/.bin/prettier`` never reaches npx: lintro resolves and
#: probes that concrete binary instead.
_REFUSED: list[tuple[str, Callable[[Path], Path], _Configure, str, str]] = [
    (
        "hoisted-older-pin-elsewhere",
        _hoisted_with_pin_elsewhere,
        None,
        f"registry {_PIN}",
        "a node_modules tree exists",
    ),
    (
        "hoisted-older-no-pin",
        _hoisted_no_pin,
        None,
        f"registry {_PIN}",
        "a node_modules tree exists",
    ),
    (
        "nested-pin-no-bin",
        _nested_pin_no_bin,
        None,
        f"registry {_PIN}",
        "a node_modules tree exists",
    ),
    (
        "nested-pin-no-bin-at-root",
        _at_root(_nested_pin_no_bin),
        None,
        "not found",
        "a node_modules tree exists",
    ),
    (
        "outside-bin-no-pin",
        _outside_bin_no_pin,
        None,
        f"registry {_PIN}",
        "a node_modules tree exists",
    ),
    (
        "pnpm-transitive-pin",
        _pnpm_transitive_pin,
        None,
        f"registry {_PIN}",
        "a node_modules tree exists",
    ),
    (
        "stale-hidden-lockfile",
        _stale_hidden_lockfile,
        None,
        f"registry {_PIN}",
        "a node_modules tree exists",
    ),
    (
        "workspace-brace-glob",
        _brace_glob,
        None,
        f"registry {_PIN}",
        "a node_modules tree exists",
    ),
    (
        "workspace-extglob",
        _extglob,
        None,
        f"registry {_PIN}",
        "a node_modules tree exists",
    ),
    # Workspace selection from the review: the root has no node_modules, a
    # member does. The root declares workspaces, so npm loads the member's
    # tree from it whatever the flags say.
    (
        "member-tree-no-config",
        _member_tree_with_pin,
        None,
        "not found",
        "declares npm workspaces",
    ),
    (
        "member-tree-env-workspaces",
        _member_tree_with_pin,
        _env(npm_config_workspaces="true"),
        "not found",
        "declares npm workspaces",
    ),
    (
        "member-tree-env-workspace",
        _member_tree_with_pin,
        _env(npm_config_workspace="app"),
        "npm error",
        "declares npm workspaces",
    ),
    (
        "member-tree-npmrc-workspaces",
        _member_tree_with_pin,
        _project_rc("workspaces=true\n"),
        "not found",
        "declares npm workspaces",
    ),
    (
        "member-tree-npmrc-bin-links-off",
        _member_tree_without_pin,
        _project_rc("bin-links=false\nworkspaces=true\n"),
        f"registry {_PIN}",
        "declares npm workspaces",
    ),
]


@requires_npx
@pytest.mark.parametrize(
    ("build", "configure", "npx_ran", "reason"),
    [(build, conf, ran, why) for _id, build, conf, ran, why in _REFUSED],
    ids=[case[0] for case in _REFUSED],
)
def test_npx_only_with_reusable_npm_state_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    registry: str,
    build: Callable[[Path], Path],
    configure: _Configure,
    npx_ran: str,
    reason: str,
) -> None:
    """Wherever npx could reuse something, lintro refuses instead of trusting the pin.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Scoped environment override.
        registry: Localhost registry URL.
        build: Layout builder.
        configure: npm config for the layout.
        npx_ran: What lintro's generated npx command launches here.
        reason: Text the refusal must contain.
    """
    code, launched, snapshot = _probe_then_npx(
        tmp_path,
        monkeypatch,
        registry,
        build,
        configure,
    )

    assert_that(launched).contains(npx_ran)
    assert_that(snapshot.available).is_false()
    assert_that(snapshot.version).is_none()
    assert_that(snapshot.probe_error).contains(_REFUSAL).contains(reason)
    _assert_never_a_false_pass(snapshot, launched, code)


@requires_npx
@pytest.mark.parametrize(
    "key",
    ["npm_config_workspace", "NPM_CONFIG_WORKSPACE"],
)
def test_npx_only_with_a_workspace_selected_in_the_environment_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    registry: str,
    key: str,
) -> None:
    """``npm_config_workspace`` in the environment is refused before npx runs.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Scoped environment override.
        registry: Localhost registry URL.
        key: Environment variable spelling.
    """
    _require_clean_ancestry(tmp_path)

    code, launched, snapshot = _probe_then_npx(
        tmp_path,
        monkeypatch,
        registry,
        _clean_project,
        _env(**{key: "app"}),
    )

    assert_that(code).is_not_zero()
    assert_that(launched).contains("npm error")
    assert_that(snapshot.available).is_false()
    assert_that(snapshot.probe_error).contains(
        f"npm workspace selection is configured ({key}=app)",
    )
    _assert_never_a_false_pass(snapshot, launched, code)


def _relative_userconfig(_tmp: Path, cwd: Path) -> dict[str, str]:
    """Config source: a relative ``npm_config_userconfig`` (review repro 3).

    Args:
        _tmp: Temporary directory (unused).
        cwd: Execution directory, which the relative path is relative to.

    Returns:
        Extra environment naming the relative file.
    """
    _write(cwd / "customrc", "workspace=app\n")
    return {"npm_config_userconfig": "customrc"}


def _bom_project_rc(_tmp: Path, cwd: Path) -> dict[str, str]:
    """Config source: a project ``.npmrc`` with a UTF-8 BOM (review repro 4).

    Args:
        _tmp: Temporary directory (unused).
        cwd: Execution directory.

    Returns:
        No extra environment.
    """
    _write(cwd / ".npmrc", "\ufeffworkspace=app\n")
    return {}


#: ``workspace=<name>`` set in a file: lintro does not read npmrc files, so the
#: gate passes and npm fails loudly against ``--workspaces=false``.
_WORKSPACE_IN_A_FILE: list[tuple[str, Callable[[Path, Path], dict[str, str]]]] = [
    ("project-npmrc", _project_rc("workspace=app\n")),
    ("user-npmrc", _user_rc("workspace=app\n")),
    ("userconfig", _userconfig_rc("workspace[]=app\n")),
    ("globalconfig", _globalconfig_rc("workspace=app\n")),
    ("relative-userconfig", _relative_userconfig),
    ("bom-npmrc", _bom_project_rc),
]


@requires_npx
@pytest.mark.parametrize(
    "configure",
    [conf for _id, conf in _WORKSPACE_IN_A_FILE],
    ids=[case[0] for case in _WORKSPACE_IN_A_FILE],
)
def test_npx_only_with_a_workspace_selected_in_a_file_fails_loudly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    registry: str,
    configure: Callable[[Path, Path], dict[str, str]],
) -> None:
    """Npm rejects the conflict with ``--workspaces=false``; nothing else runs.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Scoped environment override.
        registry: Localhost registry URL.
        configure: Where the setting comes from.
    """
    _require_clean_ancestry(tmp_path)

    code, launched, snapshot = _probe_then_npx(
        tmp_path,
        monkeypatch,
        registry,
        _clean_project,
        configure,
    )

    assert_that(code).is_not_zero()
    assert_that(launched).contains("npm error")
    assert_that(launched).does_not_contain("registry")
    assert_that(launched).does_not_contain(_OLDER)
    _assert_never_a_false_pass(snapshot, launched, code)


def _poisoned_offline_cache(tmp: Path, _cwd: Path) -> dict[str, str]:
    """Review repro 1: a pre-filled ``_npx`` cache npm would reuse offline.

    The user cache's ``_npx`` entry for the pin holds prettier 1.0.0 beside a
    nested pin, and ``offline`` is on.

    Args:
        tmp: Temporary directory.
        _cwd: Execution directory (unused).

    Returns:
        Extra environment selecting the poisoned cache and offline mode.
    """
    key = hashlib.sha512(f"prettier@{_PIN}".encode()).hexdigest()[:16]
    tree = tmp / "npm-cache" / "_npx" / key / "node_modules"
    _package(tree / "prettier", _OLDER, label=f"cache older {_OLDER}")
    _package(tree / "other", "1.0.0", name="other", dependencies={"prettier": _PIN})
    _package(tree / "other" / "node_modules" / "prettier", _PIN)
    (tree / ".bin").mkdir(parents=True)
    (tree / ".bin" / "prettier").symlink_to("../prettier/bin.js")
    return {
        "npm_config_cache": str(tmp / "npm-cache"),
        "npm_config_offline": "true",
        "npm_config_prefer_offline": "true",
    }


def _cache_with_path_delimiter(tmp: Path, _cwd: Path) -> dict[str, str]:
    """Review repro 2: a ``cache`` value whose ``:`` injects a PATH entry.

    Args:
        tmp: Temporary directory.
        _cwd: Execution directory (unused).

    Returns:
        Extra environment with the hostile cache value.
    """
    _hoist_older(tmp / "outside")
    hostile = f"{tmp / 'outside' / 'node_modules' / '.bin'}:{tmp / 'npm-cache'}"
    return {"npm_config_cache": hostile}


def _npmrc_with_nul_byte(_tmp: Path, cwd: Path) -> dict[str, str]:
    """Review repro 5: an npmrc lintro once crashed on.

    Args:
        _tmp: Temporary directory (unused).
        cwd: Execution directory.

    Returns:
        No extra environment.
    """
    (cwd / ".npmrc").write_bytes(b"userconfig=bad\x00path\n")
    return {}


_NPM_STATE_HAZARDS: list[tuple[str, Callable[[Path, Path], dict[str, str]]]] = [
    ("poisoned-offline-cache", _poisoned_offline_cache),
    ("cache-path-delimiter", _cache_with_path_delimiter),
    ("npmrc-nul-byte", _npmrc_with_nul_byte),
]


@requires_npx
@pytest.mark.parametrize(
    "configure",
    [conf for _id, conf in _NPM_STATE_HAZARDS],
    ids=[case[0] for case in _NPM_STATE_HAZARDS],
)
def test_npx_cache_and_npmrc_hazards_still_run_the_pin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    registry: str,
    configure: Callable[[Path, Path], dict[str, str]],
) -> None:
    """Lintro's own ``--cache`` and offline flags keep the pin authoritative.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Scoped environment override.
        registry: Localhost registry URL.
        configure: The hostile npm state.
    """
    _require_clean_ancestry(tmp_path)

    code, launched, snapshot = _probe_then_npx(
        tmp_path,
        monkeypatch,
        registry,
        _clean_project,
        configure,
    )

    assert_that(snapshot.command).contains(
        f"--cache={tmp_path / 'lintro-cache' / 'npx' / f'prettier@{_PIN}'}",
    )
    assert_that(code).is_zero()
    assert_that(launched).is_equal_to(f"registry {_PIN}")
    assert_that(snapshot.version).is_equal_to(_PIN)
    _assert_never_a_false_pass(snapshot, launched, code)


@requires_npx
@pytest.mark.parametrize(
    "configure",
    [_project_rc("workspace=app\n"), _relative_userconfig, _bom_project_rc],
    ids=["project-npmrc", "relative-userconfig", "bom-npmrc"],
)
def test_lintro_check_reports_an_npm_failure_as_an_execution_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    registry: str,
    configure: Callable[[Path, Path], dict[str, str]],
) -> None:
    """End to end, npm's own error is an execution failure, never a verdict.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Scoped environment override.
        registry: Localhost registry URL.
        configure: An npmrc ``workspace`` that npm rejects.
    """
    assert _NPX is not None and _NODE is not None
    _require_clean_ancestry(tmp_path)
    project = _clean_project(tmp_path)
    (project / "data.json").write_text("{}\n", encoding="utf-8")
    extra = configure(tmp_path, project)
    env = _isolate(
        monkeypatch,
        tmp_path,
        {"npx": _NPX, "node": _NODE},
        registry,
        extra,
    )

    run = subprocess.run(  # nosec B603 - fixed argv, shell=False
        [
            sys.executable,
            "-m",
            "lintro",
            "check",
            str(project),
            "--tools",
            "prettier",
            "--output-format",
            "json",
        ],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    rows = [
        row for row in json.loads(run.stdout)["results"] if row["tool"] == "prettier"
    ]

    assert_that(run.returncode).is_not_zero()
    assert_that(rows).is_length(1)
    assert_that(rows[0]["execution_failed"]).is_true()
    assert_that(rows[0]["success"]).is_false()
    assert_that(rows[0]["issues_count"]).is_zero()
    assert_that(rows[0]["status"]).is_equal_to("failed")
    # lintro's reason is stable; npm's wording is not ("Cannot use" in npm 11,
    # "Can not use" in npm 10), so only its shape is checked.
    output = rows[0]["output"]
    assert_that(output).starts_with("npm failed before the tool produced a report:")
    assert_that(re.search(r"^npm (?:error|ERR!) ", output, re.MULTILINE)).is_not_none()
    assert_that(
        re.search(r"Can ?not use --no-workspaces and --workspace", output),
    ).is_not_none()


#: Settings the generated command's flags neutralise: lintro trusts the pin
#: and the generated command really runs it.
_NEUTRALISED: list[tuple[str, _Configure]] = [
    ("no-config", None),
    ("env-workspaces", _env(npm_config_workspaces="true")),
    ("project-npmrc-workspaces", _project_rc("workspaces=true\n")),
    ("env-bin-links-off", _env(NPM_CONFIG_BIN_LINKS="false")),
    ("project-npmrc-bin-links-off", _project_rc("bin-links=false\n")),
    ("user-npmrc-bin-links-off", _user_rc("bin-links=false\n")),
    ("globalconfig-bin-links-off", _globalconfig_rc("bin-links=false\n")),
    ("env-global", _env(npm_config_global="true")),
    ("global-prefix-older-bin", _global_prefix_with_older_bin),
]


@requires_npx
@pytest.mark.parametrize(
    "configure",
    [conf for _id, conf in _NEUTRALISED],
    ids=[case[0] for case in _NEUTRALISED],
)
def test_npx_only_in_a_clean_project_runs_the_pin_lintro_reports(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    registry: str,
    configure: _Configure,
) -> None:
    """With nothing to reuse, the generated npx command runs the reported pin.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Scoped environment override.
        registry: Localhost registry URL.
        configure: npm config the flags must neutralise.
    """
    _require_clean_ancestry(tmp_path)

    code, launched, snapshot = _probe_then_npx(
        tmp_path,
        monkeypatch,
        registry,
        _clean_project,
        configure,
    )

    assert_that(code).is_zero()
    assert_that(launched).is_equal_to(f"registry {_PIN}")
    assert_that(snapshot.available).is_true()
    assert_that(snapshot.version).is_equal_to(_PIN)
    _assert_never_a_false_pass(snapshot, launched, code)


def test_lintro_check_keeps_a_local_binarys_own_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A local prettier that prints ``npm error`` is not blamed on npm.

    Only runner commands are attributed to npm. The local binary reports the
    pin for ``--version``, then fails its check with output that mentions npm,
    as a config loading a missing plugin can.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Scoped environment override.
    """
    project = _clean_project(tmp_path)
    (project / "data.json").write_text("{}\n", encoding="utf-8")
    binary = project / "node_modules" / ".bin" / "prettier"
    binary.parent.mkdir(parents=True)
    binary.write_text(
        "#!/bin/sh\n"
        f'case "$*" in *--version*) printf "{_PIN}\\n"; exit 0 ;; esac\n'
        'printf "npm error could not load plugin\\n" >&2\n'
        'printf "[error] Cannot find package prettier-plugin-x\\n" >&2\n'
        "exit 2\n",
        encoding="utf-8",
    )
    binary.chmod(0o755)
    env = _isolate(monkeypatch, tmp_path, {}, "http://127.0.0.1:9/", {})

    run = subprocess.run(  # nosec B603 - fixed argv, shell=False
        [
            sys.executable,
            "-m",
            "lintro",
            "check",
            str(project),
            "--tools",
            "prettier",
            "--output-format",
            "json",
        ],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    rows = [
        row for row in json.loads(run.stdout)["results"] if row["tool"] == "prettier"
    ]

    assert_that(rows).is_length(1)
    assert_that(rows[0]["success"]).is_false()
    assert_that(rows[0]["output"] or "").does_not_contain(
        "npm failed before the tool produced a report",
    )
    assert_that(rows[0]["output"] or "").contains("prettier-plugin-x")


def _poison_cache_entry(cache: Path) -> Path:
    """Tamper with the ``_npx`` entry for the pin, as in the round-10 review.

    The top-level prettier becomes 1.0.0, a nested copy keeps the pin, and the
    hidden lockfile is removed, so npm reads the disk and reuses the entry.

    Args:
        cache: lintro's per-pin npm cache.

    Returns:
        The entry's ``node_modules`` directory.
    """
    key = hashlib.sha512(f"prettier@{_PIN}".encode()).hexdigest()[:16]
    tree = cache / "_npx" / key / "node_modules"
    shutil.rmtree(tree / "prettier", ignore_errors=True)
    _package(tree / "prettier", _OLDER, label=f"cache older {_OLDER}")
    _package(tree / "other", "1.0.0", name="other", dependencies={"prettier": _PIN})
    _package(tree / "other" / "node_modules" / "prettier", _PIN)
    (tree / ".package-lock.json").unlink(missing_ok=True)
    return tree


def _warm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    registry_url: str,
) -> tuple[Path, dict[str, str], list[str]]:
    """Run lintro's npx command once so its per-pin cache holds the pin.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Scoped environment override.
        registry_url: Localhost registry URL.

    Returns:
        The project, the runner environment, and lintro's npx command.
    """
    assert _NPX is not None and _NODE is not None
    project = _clean_project(tmp_path)
    env = _isolate(
        monkeypatch,
        tmp_path,
        {"npx": _NPX, "node": _NODE},
        registry_url,
        {},
    )
    command = list(snapshots.probe_tool("prettier", search_root=project).command)
    assert_that(_run(command, project, env)).is_equal_to((0, f"registry {_PIN}"))
    return project, env, command


@requires_npx
def test_tampered_lintro_cache_is_repaired_before_npx_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    registry: str,
) -> None:
    """A tampered cache entry would run 1.0.0; the gate deletes it first.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Scoped environment override.
        registry: Localhost registry URL.
    """
    _require_clean_ancestry(tmp_path)
    project, env, command = _warm(tmp_path, monkeypatch, registry)
    cache = npx_cache_dir(f"prettier@{_PIN}")

    _poison_cache_entry(cache)
    raw = _run(command, project, env)
    _poison_cache_entry(cache)
    snapshot = snapshots.probe_tool("prettier", search_root=project)
    code, launched = _run(list(snapshot.command), project, env)

    assert_that(raw).is_equal_to((0, f"cache older {_OLDER}"))
    assert_that(snapshot.available).is_true()
    assert_that(snapshot.version).is_equal_to(_PIN)
    assert_that((code, launched)).is_equal_to((0, f"registry {_PIN}"))
    _assert_never_a_false_pass(snapshot, launched, code)


@requires_npx
def test_symlinked_lintro_cache_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    registry: str,
) -> None:
    """A cache that resolves outside lintro's cache root is not trusted.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Scoped environment override.
        registry: Localhost registry URL.
    """
    _require_clean_ancestry(tmp_path)
    project, env, command = _warm(tmp_path, monkeypatch, registry)
    elsewhere = tmp_path / "elsewhere-cache"
    shutil.copytree(npx_cache_dir(f"prettier@{_PIN}"), elsewhere, symlinks=True)
    _poison_cache_entry(elsewhere)
    shutil.rmtree(npx_cache_dir(f"prettier@{_PIN}"))
    npx_cache_dir(f"prettier@{_PIN}").symlink_to(elsewhere)

    snapshot = snapshots.probe_tool("prettier", search_root=project)
    code, launched = _run(command, project, env)

    assert_that(launched).is_equal_to(f"cache older {_OLDER}")
    assert_that(snapshot.available).is_false()
    assert_that(snapshot.probe_error).contains("outside")
    _assert_never_a_false_pass(snapshot, launched, code)


@requires_npx
def test_clean_warm_lintro_cache_is_reused_without_reinstalling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    registry: str,
) -> None:
    """Validation keeps a good entry: the second run does not reinstall.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Scoped environment override.
        registry: Localhost registry URL.
    """
    _require_clean_ancestry(tmp_path)
    project, env, _command = _warm(tmp_path, monkeypatch, registry)
    key = hashlib.sha512(f"prettier@{_PIN}".encode()).hexdigest()[:16]
    manifest = (
        npx_cache_dir(f"prettier@{_PIN}")
        / "_npx"
        / key
        / "node_modules"
        / "prettier"
        / "package.json"
    )
    before = manifest.stat().st_ino

    snapshot = snapshots.probe_tool("prettier", search_root=project)
    code, launched = _run(list(snapshot.command), project, env)

    assert_that(manifest.stat().st_ino).is_equal_to(before)
    assert_that((code, launched)).is_equal_to((0, f"registry {_PIN}"))
    assert_that(snapshot.version).is_equal_to(_PIN)
    _assert_never_a_false_pass(snapshot, launched, code)


@require_command("bun", ["bun"], min_version=NO_MIN_VERSION)
def test_bunx_runs_the_pin_beside_a_hoisted_copy_and_the_gate_reports_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    registry: str,
) -> None:
    """Bun fetches the pin from the registry and runs it, never the local tree.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Scoped environment override.
        registry: Localhost registry URL, served to bun too.
    """
    bunx = shutil.which("bunx")
    assert bunx is not None
    cwd = _hoisted_with_pin_elsewhere(tmp_path)
    env = _isolate(monkeypatch, tmp_path, {"bunx": bunx}, registry, {})

    snapshot = snapshots.probe_tool("prettier", search_root=cwd)
    code, launched = _run(list(snapshot.command), cwd, env)

    assert_that(snapshot.command[0]).is_equal_to("bunx")
    assert_that(snapshot.available).is_true()
    assert_that(snapshot.version).is_equal_to(_PIN)
    assert_that(code).is_zero()
    assert_that(launched).is_equal_to(f"registry {_PIN}")

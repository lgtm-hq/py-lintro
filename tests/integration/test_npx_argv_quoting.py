r"""Real npx: path arguments reach the tool literally, never shell-expanded (#3439).

npx runs the tool through ``sh -c`` (or npm's configured ``script-shell``) and
quotes only arguments carrying ``\t\n\r "#$&'()*;<>?\\`|~``. A name such as
``[slug].css`` therefore globbed to a neighbouring ``s.css``, and under a bash
``script-shell`` ``a{1,2}.css`` brace-expanded to ``a1.css a2.css``.

Each test builds the exact command lintro's Node builder generates for the npx
fallback, appends path arguments, and runs it through lintro's own subprocess
executor beside decoys that the shell would expand those names to. A
localhost registry serves a fake pinned ``prettier`` whose binary prints the
argv it received, so the assertion is what the tool saw. Nothing reaches the
internet.

npx is gated with a plain skip, as in ``test_registry_runner_hoisted_bin``:
the tools image ships bun but no npm.
"""

from __future__ import annotations

import io
import json
import shutil
import tarfile
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro._tool_versions import get_tool_version
from lintro.enums.tool_name import ToolName
from lintro.plugins.subprocess_executor import (
    run_subprocess,
    run_subprocess_streaming,
)
from lintro.tools.core.command_builders import NodeJSBuilder
from lintro.utils import file_cache

_PIN = get_tool_version("prettier") or ""
_NPX = shutil.which("npx")
_NODE = shutil.which("node")
_BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(
    _NPX is None or _NODE is None or not _PIN,
    reason="npx and node are required; the tools image ships bun only",
)

#: Names the shell would expand, and the decoys they would expand to.
_TARGETS: tuple[str, ...] = ("[slug].css", "a{1,2}.css", "src/[...rest]/x.css")
_DECOYS: tuple[str, ...] = ("s.css", "a1.css", "a2.css", "src/r/x.css")


def _echo_tarball() -> bytes:
    """Build a ``prettier`` tarball whose binary prints its argv as JSON.

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
            b"#!/usr/bin/env node\n"
            b"console.log(JSON.stringify(process.argv.slice(2)))\n",
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
    """Serve the argv-echoing ``prettier`` pin from localhost.

    Yields:
        str: The registry URL.
    """
    tarball = _echo_tarball()
    ports: list[int] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path.rstrip("/") == "/prettier":
                tarball_url = f"http://127.0.0.1:{ports[0]}/prettier-{_PIN}.tgz"
                version = {
                    "name": "prettier",
                    "version": _PIN,
                    "bin": {"prettier": "bin.js"},
                    "dist": {"tarball": tarball_url},
                }
                body = json.dumps(
                    {
                        "name": "prettier",
                        "dist-tags": {"latest": _PIN},
                        "versions": {_PIN: version},
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


def _npx_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    registry_url: str,
    script_shell: str,
) -> tuple[Path, list[str]]:
    """Isolate npm, lay out targets and decoys, and build lintro's npx command.

    Only ``npx`` and ``node`` are on ``PATH``, so the builder lands on the npx
    fallback exactly as on a machine without Bun.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Scoped environment override.
        registry_url: Localhost registry URL.
        script_shell: Shell npm runs the tool through.

    Returns:
        The project directory and the generated npx command.
    """
    assert _NPX is not None and _NODE is not None
    shim = tmp_path / "path"
    shim.mkdir()
    (shim / "npx").symlink_to(_NPX)
    (shim / "node").symlink_to(_NODE)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(file_cache, "CACHE_DIR", tmp_path / "lintro-cache")
    monkeypatch.setenv("PATH", f"{shim}:/bin:/usr/bin")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("npm_config_registry", registry_url)
    monkeypatch.setenv("npm_config_cache", str(tmp_path / "npm-cache"))
    monkeypatch.setenv("npm_config_update_notifier", "false")
    monkeypatch.setenv("npm_config_audit", "false")
    monkeypatch.setenv("npm_config_fund", "false")
    monkeypatch.setenv("npm_config_script_shell", script_shell)

    project = tmp_path / "proj"
    for name in (*_TARGETS, *_DECOYS):
        (project / name).parent.mkdir(parents=True, exist_ok=True)
        (project / name).write_text("a{color:red}\n", encoding="utf-8")
    command = NodeJSBuilder().get_command_in(
        "prettier",
        ToolName.PRETTIER,
        project,
    )
    assert_that(command[0]).is_equal_to("npx")
    return project, command


@pytest.mark.parametrize(
    "script_shell",
    [
        "sh",
        pytest.param(
            "bash",
            marks=pytest.mark.skipif(_BASH is None, reason="bash is not installed"),
        ),
    ],
)
def test_npx_fallback_passes_glob_and_brace_names_literally(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    registry: str,
    script_shell: str,
) -> None:
    """``[slug]`` and ``a{1,2}`` reach the tool as written, not as the decoys.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Scoped environment override.
        registry: Localhost registry URL.
        script_shell: Shell npm runs the tool through; bash brace-expands.
    """
    project, command = _npx_command(tmp_path, monkeypatch, registry, script_shell)
    argv = ["--check", *_TARGETS, "it's here.css"]

    result = run_subprocess(
        [*command, *argv],
        timeout=180,
        cwd=str(project),
    )

    assert_that(result.returncode).described_as(result.output).is_zero()
    received = json.loads(result.stdout.strip().splitlines()[-1])
    assert_that(received).is_equal_to(argv)
    assert_that(received).does_not_contain(*_DECOYS)


def test_npx_fallback_streaming_passes_names_literally(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    registry: str,
) -> None:
    """The streaming executor quotes npx arguments the same way.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Scoped environment override.
        registry: Localhost registry URL.
    """
    project, command = _npx_command(tmp_path, monkeypatch, registry, "sh")

    result = run_subprocess_streaming(
        [*command, "[slug].css"],
        timeout=180,
        cwd=str(project),
    )

    assert_that(result.returncode).described_as(result.output).is_zero()
    received = json.loads(result.stdout.strip().splitlines()[-1])
    assert_that(received).is_equal_to(["[slug].css"])

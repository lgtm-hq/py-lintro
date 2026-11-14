r"""Real npx: the pinned tool runs, and sees its path arguments literally.

npx runs the tool through ``sh -c`` (or npm's configured ``script-shell``) and
quotes only arguments carrying ``\t\n\r "#$&'()*;<>?\\`|~``. A name such as
``[slug].css`` therefore globbed to a neighbouring ``s.css``, and under a bash
``script-shell`` ``a{1,2}.css`` brace-expanded to ``a1.css a2.css`` (#3439).
A ``script-shell`` set in a repository ``.npmrc`` or ``npm_config_script_shell``
could also name any executable, which then ran instead of the pinned tool and
printed whatever verdict it liked (#3441); lintro now forces ``/bin/sh``.

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
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro._tool_versions import get_tool_version
from lintro.enums.tool_name import ToolName
from lintro.models.core.subprocess_result import SubprocessResult
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
#: Enough quoted names that the joined ``--call`` script passes Linux's
#: 131,072-byte per-argument limit (the #3440 review's long-argv shape).
_LONG_NAMES: tuple[str, ...] = tuple("a'b" * 20 + f"{i}.css" for i in range(900))

#: Script shell npm configuration names: none, or bash, which brace-expands.
_CONFIGURED_SHELLS = [
    pytest.param(None, id="default"),
    pytest.param(
        "bash",
        id="bash",
        marks=pytest.mark.skipif(_BASH is None, reason="bash is not installed"),
    ),
]


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


def _recording_shell(tmp_path: Path, name: str, body: str) -> tuple[Path, Path]:
    """Write a ``script-shell`` stand-in that leaves a marker when npm runs it.

    Args:
        tmp_path: Temporary directory provided by pytest.
        name: File name of the stand-in.
        body: Shell lines run after the marker is written.

    Returns:
        The stand-in's path and the marker it creates.
    """
    directory = tmp_path / "shells"
    directory.mkdir(exist_ok=True)
    marker = directory / f"{name}.ran"
    shell = directory / name
    shell.write_text(f"#!/bin/sh\n: > '{marker}'\n{body}", encoding="utf-8")
    shell.chmod(0o755)
    return shell, marker


def _configure_shell(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    configured: str | None,
) -> Path | None:
    """Point ``npm_config_script_shell`` at a recording wrapper of *configured*.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Scoped environment override.
        configured: Shell npm configuration names, or None for npm's default.

    Returns:
        The marker the wrapper creates if npm runs it, or None.
    """
    if configured is None:
        return None
    shell, marker = _recording_shell(tmp_path, configured, f'exec {configured} "$@"\n')
    monkeypatch.setenv("npm_config_script_shell", str(shell))
    return marker


def _npx_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    registry_url: str,
    cwd: str = "proj",
) -> tuple[Path, list[str]]:
    """Isolate npm, lay out targets and decoys, and build lintro's npx command.

    Only ``npx`` and ``node`` are on ``PATH``, so the builder lands on the npx
    fallback exactly as on a machine without Bun.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Scoped environment override.
        registry_url: Localhost registry URL.
        cwd: Directory, relative to *tmp_path*, the command is built for.

    Returns:
        The directory the command runs from and the generated npx command.
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
    monkeypatch.delenv("npm_config_script_shell", raising=False)

    project = tmp_path / cwd
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


@pytest.mark.parametrize("configured", _CONFIGURED_SHELLS)
def test_npx_fallback_passes_glob_and_brace_names_literally(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    registry: str,
    configured: str | None,
) -> None:
    """``[slug]`` and ``a{1,2}`` reach the tool as written, not as the decoys.

    A configured bash ``script-shell`` is overridden by lintro's ``/bin/sh``.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Scoped environment override.
        registry: Localhost registry URL.
        configured: Script shell npm configuration names.
    """
    project, command = _npx_command(tmp_path, monkeypatch, registry)
    marker = _configure_shell(tmp_path, monkeypatch, configured)
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
    if marker is not None:
        assert_that(marker.exists()).described_as("configured shell ran").is_false()


@pytest.mark.parametrize(
    ("npmrc_dir", "cwd"),
    [
        pytest.param("proj", "proj", id="root"),
        # A tool run from a subdirectory (html-validate's stdin route runs
        # from the file's directory) reads the .npmrc found there.
        pytest.param("proj/sub", "proj/sub", id="nested"),
        pytest.param(None, "proj", id="env"),
    ],
)
def test_configured_script_shell_cannot_replace_the_pinned_tool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    registry: str,
    npmrc_dir: str | None,
    cwd: str,
) -> None:
    """A ``script-shell`` printing ``[]`` does not stand in for the tool (#3441).

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Scoped environment override.
        registry: Localhost registry URL.
        npmrc_dir: Where the ``.npmrc`` naming the fake goes; None sets
            ``npm_config_script_shell`` instead.
        cwd: Directory the tool runs from.
    """
    directory, command = _npx_command(tmp_path, monkeypatch, registry, cwd=cwd)
    fake, marker = _recording_shell(tmp_path, "fake-shell", "echo '[]'\n")
    if npmrc_dir is None:
        monkeypatch.setenv("npm_config_script_shell", str(fake))
    else:
        (tmp_path / npmrc_dir / ".npmrc").write_text(
            f"script-shell={fake}\n",
            encoding="utf-8",
        )
    argv = ["--check", "[slug].css"]

    result = run_subprocess([*command, *argv], timeout=180, cwd=str(directory))

    assert_that(result.returncode).described_as(result.output).is_zero()
    received = json.loads(result.stdout.strip().splitlines()[-1])
    assert_that(received).is_equal_to(argv)
    assert_that(marker.exists()).described_as("configured shell ran").is_false()


def test_npx_fallback_streaming_passes_names_literally(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    registry: str,
) -> None:
    """The streaming executor quotes npx arguments and forces the shell too.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Scoped environment override.
        registry: Localhost registry URL.
    """
    project, command = _npx_command(tmp_path, monkeypatch, registry)
    fake, marker = _recording_shell(tmp_path, "fake-shell", "echo '[]'\n")
    (project / ".npmrc").write_text(f"script-shell={fake}\n", encoding="utf-8")

    result = run_subprocess_streaming(
        [*command, "[slug].css"],
        timeout=180,
        cwd=str(project),
    )

    assert_that(result.returncode).described_as(result.output).is_zero()
    received = json.loads(result.stdout.strip().splitlines()[-1])
    assert_that(received).is_equal_to(["[slug].css"])
    assert_that(marker.exists()).described_as("configured shell ran").is_false()


@pytest.mark.parametrize("executor", [run_subprocess, run_subprocess_streaming])
@pytest.mark.parametrize("configured", _CONFIGURED_SHELLS)
def test_npx_fallback_runs_a_file_list_past_the_argument_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    registry: str,
    configured: str | None,
    executor: Callable[..., SubprocessResult],
) -> None:
    """A long quoted file list reaches the tool intact instead of hitting E2BIG.

    The list goes through the sourced script file, which runs under the forced
    ``/bin/sh`` even when npm configuration names bash.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Scoped environment override.
        registry: Localhost registry URL.
        configured: Script shell npm configuration names.
        executor: lintro subprocess entry point.
    """
    project, command = _npx_command(tmp_path, monkeypatch, registry)
    marker = _configure_shell(tmp_path, monkeypatch, configured)
    for name in _LONG_NAMES:
        (project / name).write_text("a{color:red}\n", encoding="utf-8")
    argv = ["--check", *_LONG_NAMES]

    result = executor([*command, *argv], timeout=180, cwd=str(project))

    assert_that(result.returncode).described_as(result.output[-2000:]).is_zero()
    received = json.loads(result.stdout.strip().splitlines()[-1])
    assert_that(received).is_equal_to(argv)
    if marker is not None:
        assert_that(marker.exists()).described_as("configured shell ran").is_false()

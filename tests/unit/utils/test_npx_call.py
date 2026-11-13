"""Tests for quoting npx fallback arguments past npm's shell (#3439)."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess  # nosec B404 - runs /bin/sh and a fake runner with fixed argv
import sys
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.plugins.subprocess_executor import (
    run_subprocess,
    run_subprocess_streaming,
)
from lintro.tools.core.node_fallback import npx_runner_options
from lintro.utils.npx_call import npx_shell_safe_command

_SPEC = "prettier@3.9.4"
_NAMES = ["[slug].ts", "a{1,2}.ts", "src/[...rest]/page.ts", "x,y.ts", "it's.ts"]
_DECOYS = ["s.ts", "a1.ts", "a2.ts", "src/r/page.ts"]

posix_only = pytest.mark.skipif(
    sys.platform == "win32",
    reason="npm's script shell is cmd.exe on Windows",
)


def _builder_npx(binary: str = "prettier") -> list[str]:
    """Return the npx fallback shape lintro's Node builder emits.

    Args:
        binary: Executable the package provides.

    Returns:
        The npx command without tool arguments.
    """
    return ["npx", "--yes", *npx_runner_options(_SPEC), "--package", _SPEC, binary]


def test_npx_arguments_move_into_one_quoted_call_script() -> None:
    """Binary and arguments become a single ``--call`` script after the spec."""
    command = [*_builder_npx(), "--check", *_NAMES]

    rewritten = npx_shell_safe_command(command, windows=False)

    prefix = command[: command.index(_SPEC) + 1]
    assert_that(rewritten[: len(prefix)]).is_equal_to(prefix)
    assert_that(rewritten[len(prefix) :]).is_length(2)
    assert_that(rewritten[len(prefix)]).is_equal_to("--call")
    assert_that(shlex.split(rewritten[-1])).is_equal_to(
        ["prettier", "--check", *_NAMES],
    )


def test_npx_call_script_keeps_a_differing_binary_name() -> None:
    """``--package spec tsc`` runs ``tsc`` from the package, not the spec."""
    rewritten = npx_shell_safe_command(
        ["/usr/bin/npx", "--yes", "-p", "typescript@6.0.3", "tsc", "[id].ts"],
        windows=False,
    )

    assert_that(rewritten).is_equal_to(
        ["/usr/bin/npx", "--yes", "-p", "typescript@6.0.3", "--call", "tsc '[id].ts'"],
    )


@pytest.mark.parametrize(
    "command",
    [
        pytest.param(["bunx", _SPEC, "[slug].ts"], id="bunx-same-name"),
        pytest.param(
            ["bunx", "--package", _SPEC, "prettier", "[slug].ts"],
            id="bunx-package",
        ),
        pytest.param(["/repo/node_modules/.bin/prettier", "[slug].ts"], id="local"),
        pytest.param(["prettier", "a{1,2}.ts"], id="path"),
        pytest.param(["npx", "prettier", "[slug].ts"], id="npx-no-package"),
        pytest.param(["npx", "--yes", "--package", _SPEC], id="npx-no-binary"),
        pytest.param([], id="empty"),
    ],
)
def test_commands_other_than_the_npx_fallback_are_unchanged(
    command: list[str],
) -> None:
    """Bun, direct binaries and unrecognised npx shapes pass through as-is.

    Args:
        command: A command lintro may spawn.
    """
    assert_that(npx_shell_safe_command(command, windows=False)).is_equal_to(command)


def test_npx_on_windows_is_unchanged() -> None:
    """cmd.exe does not glob, and POSIX quoting would be wrong for it."""
    command = [*_builder_npx(), "[slug].ts"]

    assert_that(npx_shell_safe_command(command, windows=True)).is_equal_to(command)


@posix_only
@pytest.mark.parametrize("shell", ["sh", "bash"])
def test_call_script_survives_the_shell_beside_decoys(
    tmp_path: Path,
    shell: str,
) -> None:
    """The script npm hands to ``sh -c`` yields the names, never the decoys.

    Args:
        tmp_path: Temporary directory provided by pytest.
        shell: Script shell npm would use; bash also brace-expands.
    """
    if shutil.which(shell) is None:
        pytest.skip(f"{shell} is not installed")
    for name in [*_NAMES, *_DECOYS]:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).touch()
    rewritten = npx_shell_safe_command(
        ["npx", "--yes", "--package", "x@1.0.0", "printf", "%s\\n", *_NAMES],
        windows=False,
    )

    result = subprocess.run(  # nosec B603 - fixed argv, shell=False
        [shell, "-c", rewritten[-1]],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )

    assert_that(result.stdout.splitlines()).is_equal_to(_NAMES)


def _fake_runner(directory: Path, name: str) -> Path:
    """Write an executable *name* that prints each argument on its own line.

    Args:
        directory: Where to create the runner.
        name: Runner file name (``npx`` or ``bunx``).

    Returns:
        Path to the runner.
    """
    runner = directory / name
    runner.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n', encoding="utf-8")
    runner.chmod(0o755)
    return runner


@posix_only
def test_executor_spawns_npx_with_the_call_script(tmp_path: Path) -> None:
    """``run_subprocess`` hands npx the quoted script, not bare arguments.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    npx = _fake_runner(tmp_path, "npx")

    result = run_subprocess(
        [str(npx), "--yes", "--package", _SPEC, "prettier", "[slug].ts"],
        timeout=30,
        env={"PATH": os.environ.get("PATH", "")},
    )

    assert_that(result.stdout.splitlines()).is_equal_to(
        ["--yes", "--package", _SPEC, "--call", "prettier '[slug].ts'"],
    )


@posix_only
def test_streaming_executor_leaves_bunx_arguments_alone(tmp_path: Path) -> None:
    """Under bunx the tool's argv is spawned exactly as built.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    bunx = _fake_runner(tmp_path, "bunx")

    result = run_subprocess_streaming(
        [str(bunx), "--package", _SPEC, "prettier", "[slug].ts"],
        timeout=30,
    )

    assert_that(result.stdout.splitlines()).is_equal_to(
        ["--package", _SPEC, "prettier", "[slug].ts"],
    )

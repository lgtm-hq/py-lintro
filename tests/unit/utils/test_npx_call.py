"""Tests for quoting npx fallback arguments past npm's shell (#3439, #3441)."""

from __future__ import annotations

import os
import shlex
import subprocess  # nosec B404 - runs /bin/sh and a fake runner with fixed argv
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.models.core.subprocess_result import SubprocessResult
from lintro.plugins.subprocess_executor import (
    run_subprocess,
    run_subprocess_streaming,
)
from lintro.tools.core.node_fallback import npx_runner_options
from lintro.utils.npx_call import (
    NPX_INLINE_SCRIPT_LIMIT,
    NPX_SCRIPT_SHELL,
    NPX_SCRIPT_SHELL_OPTION,
    npx_shell_safe_command,
)

_SPEC = "prettier@3.9.4"
_NAMES = ["[slug].ts", "a{1,2}.ts", "src/[...rest]/page.ts", "x,y.ts", "it's.ts"]
_DECOYS = ["s.ts", "a1.ts", "a2.ts", "src/r/page.ts"]
#: The review's long-argv shape: 900 names whose quotes shlex escapes in five
#: bytes each, so the joined script passes Linux's 131,072-byte string limit.
_LONG_NAMES = ["a'b" * 20 + f"{i}.css" for i in range(900)]

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


def _spawned(command: list[str], *, windows: bool = False) -> list[str]:
    """Return the argv the executor would spawn for an inline-sized command.

    Args:
        command: Command lintro built.
        windows: Whether npx runs on Windows.

    Returns:
        The rewritten argv.
    """
    with npx_shell_safe_command(command, windows=windows) as spawned:
        return spawned


@pytest.fixture
def temp_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point :mod:`tempfile` at an empty directory the test can inspect.

    Args:
        tmp_path: Temporary directory provided by pytest.
        monkeypatch: Scoped attribute override.

    Returns:
        The directory new temporary files land in.
    """
    directory = tmp_path / "tmp"
    directory.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(directory))
    return directory


def test_npx_arguments_move_into_one_quoted_call_script() -> None:
    """Binary and arguments become a single ``--call`` script after the spec."""
    command = [*_builder_npx(), "--check", *_NAMES]

    rewritten = _spawned(command)

    package = command.index("--package")
    assert_that(rewritten[:package]).is_equal_to(command[:package])
    assert_that(rewritten[package:]).is_length(5)
    assert_that(rewritten[package : package + 4]).is_equal_to(
        [NPX_SCRIPT_SHELL_OPTION, "--package", _SPEC, "--call"],
    )
    assert_that(shlex.split(rewritten[-1])).is_equal_to(
        ["prettier", "--check", *_NAMES],
    )


def test_npx_call_script_keeps_a_differing_binary_name() -> None:
    """``--package spec tsc`` runs ``tsc`` from the package, not the spec."""
    rewritten = _spawned(
        ["/usr/bin/npx", "--yes", "-p", "typescript@6.0.3", "tsc", "[id].ts"],
    )

    assert_that(rewritten).is_equal_to(
        [
            "/usr/bin/npx",
            "--yes",
            NPX_SCRIPT_SHELL_OPTION,
            "-p",
            "typescript@6.0.3",
            "--call",
            "tsc '[id].ts'",
        ],
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
    assert_that(_spawned(command)).is_equal_to(command)


def test_npx_on_windows_is_unchanged() -> None:
    """cmd.exe does not glob, and POSIX quoting would be wrong for it."""
    command = [*_builder_npx(), "[slug].ts"]

    assert_that(_spawned(command, windows=True)).is_equal_to(command)


def test_short_script_stays_inline_without_a_file(temp_dir: Path) -> None:
    """A script within the limit is passed inline and writes nothing.

    Args:
        temp_dir: Where a script file would be written.
    """
    command = [*_builder_npx(), "x" * (NPX_INLINE_SCRIPT_LIMIT - len("prettier "))]

    with npx_shell_safe_command(command, windows=False) as spawned:
        assert_that(list(temp_dir.iterdir())).is_empty()

    assert_that(spawned[-2:]).is_equal_to(["--call", shlex.join(command[-2:])])


def test_long_script_is_sourced_from_a_file_deleted_on_exit(temp_dir: Path) -> None:
    """Past the limit, ``--call`` sources a script file that exists only in scope.

    Args:
        temp_dir: Where the script file is written.
    """
    command = [*_builder_npx(), "--check", *_LONG_NAMES]

    with npx_shell_safe_command(command, windows=False) as spawned:
        files = list(temp_dir.iterdir())
        assert_that(files).is_length(1)
        content = files[0].read_bytes()

    package = command.index("--package")
    assert_that(spawned[:-1]).is_equal_to(
        [
            *command[:package],
            NPX_SCRIPT_SHELL_OPTION,
            *command[package:-902],
            "--call",
        ],
    )
    assert_that(spawned[-1]).is_equal_to(f". {shlex.quote(str(files[0]))}")
    assert_that(len(spawned[-1])).is_less_than(NPX_INLINE_SCRIPT_LIMIT)
    assert_that(content).is_equal_to(
        os.fsencode(shlex.join(command[-902:])) + b"\n",
    )
    assert_that(list(temp_dir.iterdir())).is_empty()


def test_script_file_is_deleted_when_the_run_raises(temp_dir: Path) -> None:
    """A failing spawn inside the scope still removes the script file.

    Args:
        temp_dir: Where the script file is written.

    Raises:
        OSError: Stands in for a failed spawn.
    """
    command = [*_builder_npx(), *_NAMES]

    with (
        pytest.raises(OSError, match="spawn failed"),
        npx_shell_safe_command(command, windows=False, inline_limit=0),
    ):
        assert_that(list(temp_dir.iterdir())).is_length(1)
        raise OSError("spawn failed")

    assert_that(list(temp_dir.iterdir())).is_empty()


def _forced_shell(argv: list[str]) -> str:
    """Return the ``script-shell`` a rewritten npx command forces.

    Args:
        argv: Rewritten npx command.

    Returns:
        The shell named by its ``--script-shell=`` option.
    """
    options = [arg for arg in argv if arg.startswith("--script-shell=")]
    assert_that(options).is_length(1)
    return options[0].removeprefix("--script-shell=")


@posix_only
@pytest.mark.parametrize("inline_limit", [NPX_INLINE_SCRIPT_LIMIT, 0])
def test_call_script_survives_the_forced_shell_beside_decoys(
    tmp_path: Path,
    inline_limit: int,
) -> None:
    """Under the forced ``/bin/sh`` the script yields the names, never the decoys.

    Args:
        tmp_path: Temporary directory provided by pytest.
        inline_limit: Inline script limit; 0 forces the script file.
    """
    for name in [*_NAMES, *_DECOYS]:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).touch()

    with npx_shell_safe_command(
        ["npx", "--yes", "--package", "x@1.0.0", "printf", "%s\\n", *_NAMES],
        windows=False,
        inline_limit=inline_limit,
    ) as rewritten:
        shell = _forced_shell(rewritten)
        result = subprocess.run(  # nosec B603 - fixed argv, shell=False
            [shell, "-c", rewritten[-1]],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )

    assert_that(shell).is_equal_to(NPX_SCRIPT_SHELL)
    assert_that(result.stdout.splitlines()).is_equal_to(_NAMES)


#: A stand-in ``npx`` that picks the script shell the way npm does: a
#: ``--script-shell=`` flag beats ``npm_config_script_shell``, which beats
#: ``sh``. It reports the shell on stderr and runs the ``--call`` script with it.
_NPM_LIKE_NPX = """shell=${npm_config_script_shell:-sh}
for arg; do
  case $arg in --script-shell=*) shell=${arg#--script-shell=} ;; esac
  last=$arg
done
echo "script-shell: $shell" >&2
exec "$shell" -c "$last"
"""


@posix_only
@pytest.mark.parametrize("configured", ["bash", "fake"])
def test_configured_script_shell_is_overridden_by_the_forced_shell(
    tmp_path: Path,
    configured: str,
) -> None:
    """A ``script-shell`` from npm configuration no longer runs the tool.

    ``bash`` is the shell #3440's quoting was checked under; ``fake`` stands
    for a repository ``.npmrc`` naming a script that prints ``[]``.

    Args:
        tmp_path: Temporary directory provided by pytest.
        configured: Script shell npm configuration names.
    """
    bin_dir = tmp_path / "bin"
    npx = _executable(bin_dir, "npx", _NPM_LIKE_NPX)
    fake = _executable(bin_dir, "fake-shell", "echo '[]'\n")
    project = tmp_path / "project"
    for name in [*_NAMES, *_DECOYS]:
        (project / name).parent.mkdir(parents=True, exist_ok=True)
        (project / name).touch()
    script_shell = str(fake) if configured == "fake" else configured

    result = run_subprocess(
        [str(npx), "--yes", "--package", _SPEC, "printf", "%s\\n", *_NAMES],
        timeout=30,
        cwd=str(project),
        env={"npm_config_script_shell": script_shell},
    )

    assert_that(result.stderr.strip()).is_equal_to(
        f"script-shell: {NPX_SCRIPT_SHELL}",
    )
    assert_that(result.stdout.splitlines()).is_equal_to(_NAMES)


@posix_only
@pytest.mark.parametrize(
    ("body", "expected"),
    [
        pytest.param("kill -TERM $$\n", 128 + 15, id="signal"),
        pytest.param("exit 3\n", 3, id="exit-code"),
    ],
)
def test_script_file_keeps_the_shell_as_the_tools_parent(
    tmp_path: Path,
    body: str,
    expected: int,
) -> None:
    """Sourced under the forced shell, the tool's status survives: 128+N or N.

    The sourced file must not ``exec`` the tool: the shell stays its parent
    and reports the signal as an exit status, as ``sh -c`` does under dash,
    rather than npm seeing the shell itself killed.

    Args:
        tmp_path: Temporary directory provided by pytest.
        body: Tool script body.
        expected: Exit status npm should see.
    """
    tool = _executable(tmp_path, "tool", body)

    with npx_shell_safe_command(
        ["npx", "--yes", "--package", "x@1.0.0", str(tool), "a.css"],
        windows=False,
        inline_limit=0,
    ) as rewritten:
        result = subprocess.run(  # nosec B603 - fixed argv, shell=False
            [_forced_shell(rewritten), "-c", rewritten[-1]],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )

    assert_that(rewritten[-1]).starts_with(". ")
    assert_that(result.returncode).is_equal_to(expected)


def _executable(directory: Path, name: str, body: str) -> Path:
    """Write an executable shell script *name* into *directory*.

    Args:
        directory: Where to create it.
        name: File name.
        body: Script body after the ``#!/bin/sh`` line.

    Returns:
        Path to the script.
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(f"#!/bin/sh\n{body}", encoding="utf-8")
    path.chmod(0o755)
    return path


_PRINT_ARGS = 'printf "%s\\n" "$@"\n'


@posix_only
def test_executor_spawns_npx_with_the_call_script(tmp_path: Path) -> None:
    """``run_subprocess`` hands npx the quoted script, not bare arguments.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    npx = _executable(tmp_path, "npx", _PRINT_ARGS)

    result = run_subprocess(
        [str(npx), "--yes", "--package", _SPEC, "prettier", "[slug].ts"],
        timeout=30,
        env={"PATH": os.environ.get("PATH", "")},
    )

    assert_that(result.stdout.splitlines()).is_equal_to(
        [
            "--yes",
            NPX_SCRIPT_SHELL_OPTION,
            "--package",
            _SPEC,
            "--call",
            "prettier '[slug].ts'",
        ],
    )


@posix_only
def test_streaming_executor_leaves_bunx_arguments_alone(tmp_path: Path) -> None:
    """Under bunx the tool's argv is spawned exactly as built.

    Args:
        tmp_path: Temporary directory provided by pytest.
    """
    bunx = _executable(tmp_path, "bunx", _PRINT_ARGS)

    result = run_subprocess_streaming(
        [str(bunx), "--package", _SPEC, "prettier", "[slug].ts"],
        timeout=30,
    )

    assert_that(result.stdout.splitlines()).is_equal_to(
        ["--package", _SPEC, "prettier", "[slug].ts"],
    )


@posix_only
@pytest.mark.parametrize("executor", [run_subprocess, run_subprocess_streaming])
def test_executors_run_a_long_npx_file_list_and_clean_up(
    tmp_path: Path,
    temp_dir: Path,
    executor: Callable[..., SubprocessResult],
) -> None:
    """A file list past the per-argument limit reaches the tool intact.

    The fake ``npx`` does what npm does with ``--call``: it runs the string
    with ``sh -c``, and the package's binary is on ``PATH``.

    Args:
        tmp_path: Temporary directory provided by pytest.
        temp_dir: Where the script file is written.
        executor: lintro subprocess entry point.
    """
    bin_dir = tmp_path / "bin"
    npx = _executable(bin_dir, "npx", 'for last; do :; done\nexec sh -c "$last"\n')
    _executable(bin_dir, "prettier", _PRINT_ARGS)

    result = executor(
        [str(npx), "--yes", "--package", _SPEC, "prettier", "--check", *_LONG_NAMES],
        timeout=60,
        env={"PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"},
    )

    assert_that(result.returncode).described_as(result.output).is_zero()
    assert_that(result.stdout.splitlines()).is_equal_to(["--check", *_LONG_NAMES])
    assert_that(list(temp_dir.iterdir())).is_empty()

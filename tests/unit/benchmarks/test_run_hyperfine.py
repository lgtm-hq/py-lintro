"""Tests for the hyperfine CLI-overhead shell drivers.

These never run a real benchmark. They lock argv parsing, suite validation,
portable chdir, and the ruff flag alignment that makes timed commands match.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess  # nosec B404 - fixed argv against repo scripts
from pathlib import Path

from assertpy import assert_that

_REPO_ROOT = Path(__file__).resolve().parents[3]
_RUN_HYPERFINE = _REPO_ROOT / "benchmarks" / "run-hyperfine.sh"
_RUN_IN_DIR = _REPO_ROOT / "benchmarks" / "hyperfine" / "run-in-dir.sh"
_SEQUENTIAL = _REPO_ROOT / "benchmarks" / "hyperfine" / "sequential-ruff-mypy.sh"


def _bash() -> str:
    """Return the bash executable path.

    Returns:
        Absolute path to bash.

    Raises:
        pytest.fail: If bash is not on PATH.
    """
    bash_path = shutil.which("bash")
    assert_that(bash_path).is_not_none()
    assert bash_path is not None
    return bash_path


def _run_script(*args: str) -> subprocess.CompletedProcess[str]:
    """Run ``run-hyperfine.sh`` with extra arguments.

    Args:
        *args: Arguments after the script path.

    Returns:
        The completed process.
    """
    return subprocess.run(  # nosec B603 - fixed argv, no shell
        [_bash(), str(_RUN_HYPERFINE), *args],
        capture_output=True,
        text=True,
        check=False,
        cwd=_REPO_ROOT,
    )


def test_hyperfine_scripts_are_strict_bash() -> None:
    """The hyperfine drivers must parse as bash and be executable."""
    bash_path = _bash()
    for script in (_RUN_HYPERFINE, _RUN_IN_DIR, _SEQUENTIAL):
        syntax = subprocess.run(  # nosec B603 - fixed argv, no shell
            [bash_path, "-n", str(script)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert_that(script.read_text(encoding="utf-8")).starts_with(
            "#!/usr/bin/env bash",
        )
        assert_that(syntax.returncode).is_equal_to(0)
        assert_that(script.stat().st_mode & stat.S_IXUSR).is_not_equal_to(0)


def test_run_hyperfine_help_exits_zero() -> None:
    """``--help`` prints usage and does not require hyperfine."""
    result = _run_script("--help")

    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("--suite NAME")
    assert_that(result.stdout).contains("all, ruff, mypy, format, multi")


def test_run_hyperfine_rejects_unknown_suite() -> None:
    """A typo in ``--suite`` exits 2 and does not mention hyperfine missing."""
    result = _run_script("--suite", "ruf")

    assert_that(result.returncode).is_equal_to(2)
    assert_that(result.stderr).contains("invalid --suite")
    assert_that(result.stderr).does_not_contain("hyperfine is not installed")


def test_run_hyperfine_aligns_ruff_work_with_direct_commands() -> None:
    """Timed lintro ruff commands disable extra stages the direct binary skips."""
    text = _RUN_HYPERFINE.read_text(encoding="utf-8")

    assert_that(text).contains("ruff:format_check=False")
    assert_that(text).contains("ruff:lint_fix=False")
    assert_that(text).contains("run-in-dir.sh")
    assert_that(text).does_not_contain("env -C")
    assert_that(text).does_not_contain("uv run --project")


def test_run_in_dir_changes_cwd_then_execs(tmp_path: Path) -> None:
    """The chdir wrapper execs the command in the given directory.

    Args:
        tmp_path: Isolated directory to chdir into.
    """
    marker = tmp_path / "here.txt"
    result = subprocess.run(  # nosec B603 - fixed argv, no shell
        [_bash(), str(_RUN_IN_DIR), str(tmp_path), "pwd"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout.strip()).is_equal_to(str(tmp_path))
    marker.write_text("ok\n", encoding="utf-8")
    listed = subprocess.run(  # nosec B603 - fixed argv, no shell
        [_bash(), str(_RUN_IN_DIR), str(tmp_path), "ls", "here.txt"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert_that(listed.returncode).is_equal_to(0)
    assert_that(listed.stdout).contains("here.txt")


def test_run_in_dir_requires_a_command() -> None:
    """Missing command argv exits 2 with usage."""
    result = subprocess.run(  # nosec B603 - fixed argv, no shell
        [_bash(), str(_RUN_IN_DIR), "/tmp"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert_that(result.returncode).is_equal_to(2)
    assert_that(result.stderr).contains("usage: run-in-dir.sh")


def test_run_hyperfine_ruff_suite_does_not_require_mypy(
    tmp_path: Path,
) -> None:
    """``--suite ruff`` must not fail only because mypy is absent.

    Args:
        tmp_path: Isolated fake PATH.
    """
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    hyperfine = fake_bin / "hyperfine"
    hyperfine.write_text(
        "#!/bin/sh\n"
        "while [ $# -gt 0 ]; do\n"
        '  case "$1" in\n'
        "    --export-json) printf '{}\\n' > \"$2\"; shift 2 ;;\n"
        "    --version) printf 'hyperfine 1.0.0\\n'; shift ;;\n"
        "    *) shift ;;\n"
        "  esac\n"
        "done\n"
        "exit 0\n",
        encoding="utf-8",
    )
    hyperfine.chmod(hyperfine.stat().st_mode | stat.S_IXUSR)
    for name in ("ruff", "lintro", "uv"):
        tool = fake_bin / name
        tool.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        tool.chmod(tool.stat().st_mode | stat.S_IXUSR)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    env["HOME"] = str(tmp_path / "home")
    env["HYPERFINE_RESULTS_DIR"] = str(tmp_path / "results")
    (tmp_path / "home").mkdir()
    (tmp_path / "results").mkdir()

    result = subprocess.run(  # nosec B603 - fixed argv, no shell
        [_bash(), str(_RUN_HYPERFINE), "--suite", "ruff", "--quick"],
        capture_output=True,
        text=True,
        check=False,
        cwd=_REPO_ROOT,
        env=env,
    )

    combined = result.stdout + result.stderr
    assert_that(combined).does_not_contain("missing tools on PATH: mypy")
    assert_that(result.returncode).is_equal_to(0)

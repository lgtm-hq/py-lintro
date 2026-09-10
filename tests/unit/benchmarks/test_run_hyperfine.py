"""Tests for the hyperfine CLI-overhead shell drivers.

These never run a real benchmark. A stub ``hyperfine`` records the argv it was
handed, so the tests assert the commands hyperfine would actually exec (native
reference vs lintro) rather than grepping the driver's source text.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess  # nosec B404 - fixed argv against repo scripts
from pathlib import Path

import pytest
from assertpy import assert_that

_REPO_ROOT = Path(__file__).resolve().parents[3]
_RUN_HYPERFINE = _REPO_ROOT / "benchmarks" / "run-hyperfine.sh"
_RUN_IN_DIR = _REPO_ROOT / "benchmarks" / "hyperfine" / "run-in-dir.sh"
_SEQUENTIAL = _REPO_ROOT / "benchmarks" / "hyperfine" / "sequential-ruff-mypy.sh"
_SEQUENTIAL_FMT = _REPO_ROOT / "benchmarks" / "hyperfine" / "sequential-ruff-fmt.sh"

_INVOCATION_MARKER = "=== invocation ==="

# Stub hyperfine: append every argument to a log, honour --export-json, exit 0.
_HYPERFINE_STUB = f"""#!/bin/sh
if [ "$1" = "--version" ]; then
  printf 'hyperfine 1.0.0\\n'
  exit 0
fi
printf '{_INVOCATION_MARKER}\\n' >> "$HYPERFINE_ARGV_LOG"
for arg in "$@"; do
  printf '%s\\n' "$arg" >> "$HYPERFINE_ARGV_LOG"
done
export_json=""
while [ $# -gt 0 ]; do
  if [ "$1" = "--export-json" ]; then
    export_json="$2"
  fi
  shift
done
if [ -n "$export_json" ]; then
  printf '{{"results": []}}\\n' > "$export_json"
fi
exit 0
"""

_SUITE_EXPECTATIONS: dict[str, tuple[str, str, str, str]] = {
    # suite -> (export file, reference name, reference substring, lintro substring)
    "ruff": (
        "ruff-check-overhead.json",
        "ruff check",
        "ruff check .",
        "--tools ruff .",
    ),
    "mypy": (
        "mypy-overhead.json",
        "mypy",
        "mypy .",
        "--tools mypy .",
    ),
    "format": (
        "ruff-format-overhead.json",
        "sequential ruff check then format",
        "sequential-ruff-fmt.sh",
        "--tools ruff .",
    ),
    "multi": (
        "multi-tool-overhead.json",
        "sequential ruff then mypy",
        "sequential-ruff-mypy.sh",
        "--tools ruff,mypy .",
    ),
}


def _bash() -> str:
    """Return the bash executable path.

    Returns:
        Absolute path to bash.
    """
    bash_path = shutil.which("bash")
    assert_that(bash_path).is_not_none()
    assert bash_path is not None
    return bash_path


def _write_executable(
    path: Path,
    body: str,
) -> None:
    """Write ``body`` to ``path`` and mark it executable.

    Args:
        path: File to create.
        body: Shell script contents.
    """
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


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


def _make_sandbox(tmp_path: Path) -> tuple[dict[str, str], Path, Path]:
    """Build an isolated environment where only stub tools are reachable.

    The driver prepends its own bin directory to ``PATH``; pointing
    ``LINTRO_BENCH_VENV_BIN`` at the stub directory keeps a real ``.venv``
    (which has mypy after ``uv sync --extra full``) out of the way.

    Args:
        tmp_path: Pytest temporary directory.

    Returns:
        Tuple of (environment, results directory, hyperfine argv log).
    """
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    results = tmp_path / "results"
    results.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    argv_log = tmp_path / "hyperfine-argv.log"

    _write_executable(fake_bin / "hyperfine", _HYPERFINE_STUB)
    for name in ("ruff", "mypy", "lintro"):
        _write_executable(fake_bin / name, "#!/bin/sh\nexit 0\n")

    env = os.environ.copy()
    # Keep only system dirs so a globally installed mypy cannot leak in.
    env["PATH"] = os.pathsep.join([str(fake_bin), "/usr/bin", "/bin"])
    env["HOME"] = str(home)
    env["HYPERFINE_RESULTS_DIR"] = str(results)
    env["HYPERFINE_ARGV_LOG"] = str(argv_log)
    env["LINTRO_BENCH_VENV_BIN"] = str(fake_bin)
    env.pop("WARMUP", None)
    env.pop("RUNS", None)
    return env, results, argv_log


def _run_sandboxed(
    env: dict[str, str],
    *args: str,
) -> subprocess.CompletedProcess[str]:
    """Run the driver inside a sandbox environment.

    Args:
        env: Environment produced by :func:`_make_sandbox`.
        *args: Driver arguments.

    Returns:
        The completed process.
    """
    return subprocess.run(  # nosec B603 - fixed argv, no shell
        [_bash(), str(_RUN_HYPERFINE), *args],
        capture_output=True,
        text=True,
        check=False,
        cwd=_REPO_ROOT,
        env=env,
    )


def _invocations(argv_log: Path) -> list[list[str]]:
    """Parse recorded hyperfine invocations from the stub's log.

    Args:
        argv_log: File written by the stub hyperfine.

    Returns:
        One argv list per invocation.
    """
    calls: list[list[str]] = []
    for line in argv_log.read_text(encoding="utf-8").splitlines():
        if line == _INVOCATION_MARKER:
            calls.append([])
        else:
            calls[-1].append(line)
    return calls


def _flag_value(
    argv: list[str],
    flag: str,
) -> str:
    """Return the value following ``flag`` in ``argv``.

    Args:
        argv: Recorded argv.
        flag: Flag whose value is wanted.

    Returns:
        The value after the flag.
    """
    assert_that(argv).contains(flag)
    return argv[argv.index(flag) + 1]


def test_hyperfine_scripts_are_strict_bash() -> None:
    """The hyperfine drivers must parse as bash and be executable."""
    bash_path = _bash()
    scripts = (_RUN_HYPERFINE, _RUN_IN_DIR, _SEQUENTIAL, _SEQUENTIAL_FMT)
    for script in scripts:
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
    assert_that(result.stdout).contains("HYPERFINE_RESULTS_DIR")


def test_run_hyperfine_rejects_unknown_suite() -> None:
    """A typo in ``--suite`` exits 2 and does not mention hyperfine missing."""
    result = _run_script("--suite", "ruf")

    assert_that(result.returncode).is_equal_to(2)
    assert_that(result.stderr).contains("invalid --suite")
    assert_that(result.stderr).does_not_contain("hyperfine is not installed")


@pytest.mark.parametrize("suite", sorted(_SUITE_EXPECTATIONS))
def test_each_suite_passes_expected_commands_to_hyperfine(
    suite: str,
    tmp_path: Path,
) -> None:
    """Every suite times its documented reference against the lintro command.

    Args:
        suite: Suite name passed to ``--suite``.
        tmp_path: Pytest temporary directory.
    """
    env, results, argv_log = _make_sandbox(tmp_path)

    result = _run_sandboxed(env, "--suite", suite, "--quick")

    assert_that(result.returncode).described_as(result.stderr).is_equal_to(0)
    calls = _invocations(argv_log)
    assert_that(calls).is_length(1)
    argv = calls[0]

    export_name, ref_name, ref_fragment, lintro_fragment = _SUITE_EXPECTATIONS[suite]
    export_path = Path(_flag_value(argv, "--export-json"))
    assert_that(export_path.name).is_equal_to(export_name)
    assert_that(export_path.parent).is_equal_to(results)
    assert_that(export_path.is_file()).is_true()
    assert_that(argv).contains("--shell=none")
    assert_that(_flag_value(argv, "--reference-name")).is_equal_to(ref_name)
    reference = _flag_value(argv, "--reference").replace("'", "")
    assert_that(reference).contains(ref_fragment)
    assert_that(_flag_value(argv, "--command-name")).contains("lintro")
    # The lintro command is the trailing positional after --command-name.
    # POSIX single-quoting wraps metacharacters (e.g. the tool comma).
    lintro_cmd = argv[-1].replace("'", "")
    assert_that(lintro_cmd).contains(lintro_fragment)
    assert_that(lintro_cmd).contains("--yes")
    assert_that(lintro_cmd).contains("run-in-dir.sh")
    if suite == "format":
        assert_that(lintro_cmd).contains("ruff:lint_fix=False")
    else:
        assert_that(lintro_cmd).contains("ruff:format_check=False")
    if suite in {"ruff", "mypy"}:
        assert_that(reference).contains("run-in-dir.sh")
        assert_that(reference).contains(" .")
    meta = json.loads((results / "baseline-meta.json").read_text(encoding="utf-8"))
    assert_that(meta["result_files"]).is_equal_to([export_name])


def test_reference_names_do_not_claim_short_circuit(tmp_path: Path) -> None:
    """The multi suite label must not imply ``&&`` short-circuit semantics.

    Args:
        tmp_path: Pytest temporary directory.
    """
    env, _results, argv_log = _make_sandbox(tmp_path)

    result = _run_sandboxed(env, "--suite", "multi", "--quick")

    assert_that(result.returncode).described_as(result.stderr).is_equal_to(0)
    argv = _invocations(argv_log)[0]
    assert_that(_flag_value(argv, "--reference-name")).does_not_contain("&&")


def test_format_suite_times_all_ruff_stages_lintro_runs(tmp_path: Path) -> None:
    """``lintro fmt`` runs three ruff stages, so the reference must too.

    Args:
        tmp_path: Pytest temporary directory.
    """
    env, _results, argv_log = _make_sandbox(tmp_path)

    result = _run_sandboxed(env, "--suite", "format", "--quick")

    assert_that(result.returncode).described_as(result.stderr).is_equal_to(0)
    argv = _invocations(argv_log)[0]
    reference = _flag_value(argv, "--reference")
    assert_that(reference).contains("sequential-ruff-fmt.sh")


def test_quick_only_fills_defaults(tmp_path: Path) -> None:
    """``--quick`` must not clobber an explicit ``--runs``/``--warmup``.

    Args:
        tmp_path: Pytest temporary directory.
    """
    env, _results, argv_log = _make_sandbox(tmp_path)

    result = _run_sandboxed(env, "--suite", "ruff", "--quick", "--runs", "20")

    assert_that(result.returncode).described_as(result.stderr).is_equal_to(0)
    argv = _invocations(argv_log)[0]
    assert_that(_flag_value(argv, "--runs")).is_equal_to("20")
    assert_that(_flag_value(argv, "--warmup")).is_equal_to("1")


def test_quick_defers_to_env_overrides(tmp_path: Path) -> None:
    """``WARMUP``/``RUNS`` env values also win over ``--quick`` defaults.

    Args:
        tmp_path: Pytest temporary directory.
    """
    env, _results, argv_log = _make_sandbox(tmp_path)
    env["WARMUP"] = "5"

    result = _run_sandboxed(env, "--suite", "ruff", "--quick")

    assert_that(result.returncode).described_as(result.stderr).is_equal_to(0)
    argv = _invocations(argv_log)[0]
    assert_that(_flag_value(argv, "--warmup")).is_equal_to("5")
    assert_that(_flag_value(argv, "--runs")).is_equal_to("3")


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


@pytest.mark.parametrize("suite", ["ruff", "format"])
def test_ruff_and_format_suites_do_not_require_mypy(
    suite: str,
    tmp_path: Path,
) -> None:
    """``--suite ruff`` and ``--suite format`` must not require mypy.

    Args:
        suite: Suite that is documented as mypy-optional.
        tmp_path: Pytest temporary directory.
    """
    env, results, argv_log = _make_sandbox(tmp_path)
    (tmp_path / "bin" / "mypy").unlink()

    result = _run_sandboxed(env, "--suite", suite, "--quick")

    combined = result.stdout + result.stderr
    assert_that(combined).does_not_contain("missing tools on PATH: mypy")
    assert_that(result.returncode).is_equal_to(0)
    argv = _invocations(argv_log)[0]
    lintro_cmd = argv[-1].replace("'", "")
    assert_that(lintro_cmd).contains("--tools ruff")
    assert_that(lintro_cmd).does_not_contain("mypy")
    export_name = _SUITE_EXPECTATIONS[suite][0]
    assert_that((results / export_name).is_file()).is_true()


def test_multi_suite_reports_missing_mypy(tmp_path: Path) -> None:
    """``--suite multi`` still fails fast with 127 when mypy is missing.

    Args:
        tmp_path: Pytest temporary directory.
    """
    env, _results, _argv_log = _make_sandbox(tmp_path)
    (tmp_path / "bin" / "mypy").unlink()

    result = _run_sandboxed(env, "--suite", "multi", "--quick")

    assert_that(result.returncode).is_equal_to(127)
    assert_that(result.stderr).contains("missing tools on PATH: mypy")


def test_sequential_runs_both_tools_and_returns_worst_status(
    tmp_path: Path,
) -> None:
    """A failing ruff must not stop mypy; the worst exit status wins.

    Args:
        tmp_path: Pytest temporary directory.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    ruff_marker = tmp_path / "ruff-ran"
    mypy_marker = tmp_path / "mypy-ran"
    _write_executable(
        bin_dir / "ruff",
        f"#!/bin/sh\n: > {ruff_marker}\nexit 1\n",
    )
    _write_executable(
        bin_dir / "mypy",
        f"#!/bin/sh\n: > {mypy_marker}\nexit 2\n",
    )

    env = os.environ.copy()
    env["RUFF_BIN"] = str(bin_dir / "ruff")
    env["MYPY_BIN"] = str(bin_dir / "mypy")
    env["LINTRO_BENCH_VENV_BIN"] = str(bin_dir)
    env["HOME"] = str(tmp_path)

    result = subprocess.run(  # nosec B603 - fixed argv, no shell
        [_bash(), str(_SEQUENTIAL)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert_that(result.returncode).is_equal_to(2)
    assert_that(ruff_marker.is_file()).is_true()
    assert_that(mypy_marker.is_file()).is_true()


def test_sequential_fmt_runs_every_stage_and_returns_worst_status(
    tmp_path: Path,
) -> None:
    """The fmt reference runs check, format --check and format regardless.

    Args:
        tmp_path: Pytest temporary directory.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "ruff-calls"
    _write_executable(
        bin_dir / "ruff",
        f'#!/bin/sh\nprintf "%s\\n" "$*" >> {calls}\n'
        'if [ "$1" = "check" ]; then exit 1; fi\nexit 0\n',
    )

    env = os.environ.copy()
    env["RUFF_BIN"] = str(bin_dir / "ruff")
    env["LINTRO_BENCH_VENV_BIN"] = str(bin_dir)
    env["HOME"] = str(tmp_path)

    result = subprocess.run(  # nosec B603 - fixed argv, no shell
        [_bash(), str(_SEQUENTIAL_FMT)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert_that(result.returncode).is_equal_to(1)
    recorded = calls.read_text(encoding="utf-8").splitlines()
    assert_that(recorded).is_equal_to(["check .", "format --check .", "format ."])


@pytest.mark.parametrize("script", [_SEQUENTIAL, _SEQUENTIAL_FMT])
def test_sequential_scripts_exit_127_when_tools_are_missing(
    script: Path,
    tmp_path: Path,
) -> None:
    """Missing binaries must reach the install hint, not abort under ``set -e``.

    Args:
        script: Sequential wrapper under test.
        tmp_path: Pytest temporary directory.
    """
    empty_bin = tmp_path / "empty"
    empty_bin.mkdir()

    env = os.environ.copy()
    # System dirs stay reachable for coreutils; neither ruff nor mypy lives there.
    env["PATH"] = os.pathsep.join([str(empty_bin), "/usr/bin", "/bin"])
    env["HOME"] = str(tmp_path)
    env["LINTRO_BENCH_VENV_BIN"] = str(empty_bin)
    env.pop("RUFF_BIN", None)
    env.pop("MYPY_BIN", None)

    result = subprocess.run(  # nosec B603 - fixed argv, no shell
        [_bash(), str(script)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert_that(result.returncode).is_equal_to(127)
    assert_that(result.stderr).contains("uv sync --dev --extra full")


def test_hyperfine_is_found_after_venv_path_rewrite(tmp_path: Path) -> None:
    """Hyperfine only in ``LINTRO_BENCH_VENV_BIN`` must still be found.

    Args:
        tmp_path: Pytest temporary directory.
    """
    env, results, argv_log = _make_sandbox(tmp_path)
    env["PATH"] = os.pathsep.join(["/usr/bin", "/bin"])

    result = _run_sandboxed(env, "--suite", "ruff", "--quick")

    assert_that(result.returncode).described_as(result.stderr).is_equal_to(0)
    assert_that(_invocations(argv_log)).is_length(1)
    assert_that((results / "ruff-check-overhead.json").is_file()).is_true()


def test_missing_hyperfine_exits_127(tmp_path: Path) -> None:
    """A missing hyperfine after PATH rewrite exits 127 with install help.

    Args:
        tmp_path: Pytest temporary directory.
    """
    env, _results, _argv_log = _make_sandbox(tmp_path)
    (tmp_path / "bin" / "hyperfine").unlink()
    env["PATH"] = os.pathsep.join(["/usr/bin", "/bin"])

    result = _run_sandboxed(env, "--suite", "ruff", "--quick")

    assert_that(result.returncode).is_equal_to(127)
    assert_that(result.stderr).contains("hyperfine is not installed")


def test_default_warmup_and_runs_without_quick(tmp_path: Path) -> None:
    """Production defaults are warmup=3 and runs=10 when ``--quick`` is omitted.

    Args:
        tmp_path: Pytest temporary directory.
    """
    env, _results, argv_log = _make_sandbox(tmp_path)

    result = _run_sandboxed(env, "--suite", "ruff")

    assert_that(result.returncode).described_as(result.stderr).is_equal_to(0)
    argv = _invocations(argv_log)[0]
    assert_that(_flag_value(argv, "--warmup")).is_equal_to("3")
    assert_that(_flag_value(argv, "--runs")).is_equal_to("10")


def test_single_suite_run_drops_stale_overhead_json(tmp_path: Path) -> None:
    """A reused results dir must not keep leftover suite JSON in metadata.

    Args:
        tmp_path: Pytest temporary directory.
    """
    env, results, _argv_log = _make_sandbox(tmp_path)
    stale = results / "mypy-overhead.json"
    foreign = results / "custom-overhead.json"
    stale.write_text('{"results": []}\n', encoding="utf-8")
    foreign.write_text('{"results": []}\n', encoding="utf-8")

    result = _run_sandboxed(env, "--suite", "ruff", "--quick")

    assert_that(result.returncode).described_as(result.stderr).is_equal_to(0)
    assert_that(stale.exists()).is_false()
    assert_that(foreign.is_file()).is_true()
    meta = json.loads((results / "baseline-meta.json").read_text(encoding="utf-8"))
    assert_that(meta["result_files"]).is_equal_to(["ruff-check-overhead.json"])
    assert_that((results / "ruff-check-overhead.json").is_file()).is_true()


def test_default_invocation_runs_all_suites(tmp_path: Path) -> None:
    """Omitting ``--suite`` runs all four exports with production warmup/runs.

    Args:
        tmp_path: Pytest temporary directory.
    """
    env, results, argv_log = _make_sandbox(tmp_path)

    result = _run_sandboxed(env)

    assert_that(result.returncode).described_as(result.stderr).is_equal_to(0)
    calls = _invocations(argv_log)
    assert_that(calls).is_length(4)
    export_names = sorted(
        Path(_flag_value(argv=argv, flag="--export-json")).name for argv in calls
    )
    expected = [
        "multi-tool-overhead.json",
        "mypy-overhead.json",
        "ruff-check-overhead.json",
        "ruff-format-overhead.json",
    ]
    assert_that(export_names).is_equal_to(expected)
    for argv in calls:
        assert_that(_flag_value(argv=argv, flag="--warmup")).is_equal_to("3")
        assert_that(_flag_value(argv=argv, flag="--runs")).is_equal_to("10")
        for token in argv:
            assert_that(token).does_not_contain("$'")
    meta = json.loads((results / "baseline-meta.json").read_text(encoding="utf-8"))
    assert_that(sorted(meta["result_files"])).is_equal_to(expected)
    for name in expected:
        assert_that((results / name).is_file()).is_true()


def test_mypy_suite_does_not_require_ruff(tmp_path: Path) -> None:
    """``--suite mypy`` must not require ruff on PATH.

    Args:
        tmp_path: Pytest temporary directory.
    """
    env, results, argv_log = _make_sandbox(tmp_path)
    (tmp_path / "bin" / "ruff").unlink()

    result = _run_sandboxed(env, "--suite", "mypy", "--quick")

    combined = result.stdout + result.stderr
    assert_that(combined).does_not_contain("missing tools on PATH: ruff")
    assert_that(result.returncode).described_as(result.stderr).is_equal_to(0)
    assert_that(_invocations(argv_log)).is_length(1)
    assert_that((results / "mypy-overhead.json").is_file()).is_true()


def test_join_cmd_survives_apostrophe_in_results_dir(tmp_path: Path) -> None:
    """Hyperfine --shell=none must receive POSIX quotes, not bash %q.

    Args:
        tmp_path: Pytest temporary directory.
    """
    sandbox_root = tmp_path / "o's bench"
    sandbox_root.mkdir()
    env, results, argv_log = _make_sandbox(sandbox_root)

    result = _run_sandboxed(env, "--suite", "ruff", "--quick")

    assert_that(result.returncode).described_as(result.stderr).is_equal_to(0)
    assert_that(results.name).is_equal_to("results")
    assert_that("o's bench" in str(results)).is_true()
    argv = _invocations(argv_log)[0]
    export_json = _flag_value(argv=argv, flag="--export-json")
    assert_that(export_json).contains("o's bench")
    assert_that(Path(export_json).is_file()).is_true()
    for token in argv:
        assert_that("$'" in token).is_false()

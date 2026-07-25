"""Tests for scripts/ci/memory-sampler.sh.

These exercise the background memory sampler wired around the Build binary
steps in build-binary.yml (#1707) using stub ``uname``/``vmstat``/``free``/
``vm_stat`` binaries on PATH. No real system probes run: the stubs emit canned
output so we can assert on platform routing and start/stop lifecycle.
"""

from __future__ import annotations

import os
import subprocess  # nosec B404 - drives the script under test with shell=False
import time
from pathlib import Path

import pytest
from assertpy import assert_that

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPT = _REPO_ROOT / "scripts/ci/memory-sampler.sh"


def _write_stub(bin_dir: Path, name: str, body: str) -> None:
    """Write an executable stub binary into a PATH shim directory.

    Args:
        bin_dir: Directory prepended to PATH for the script under test.
        name: Binary name to shadow (e.g. ``vmstat``).
        body: Bash script body executed when the stub is invoked.
    """
    stub = bin_dir / name
    stub.write_text(f"#!/usr/bin/env bash\n{body}\n")
    stub.chmod(0o755)


def _run(
    args: list[str],
    *,
    stub_bin: Path | None = None,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run memory-sampler.sh with an optional stub directory prepended to PATH.

    Args:
        args: Arguments passed to the script.
        stub_bin: Directory of stub binaries to prepend to PATH.
        extra_env: Extra environment variables for the script.

    Returns:
        subprocess.CompletedProcess[str]: The completed process.
    """
    path = os.environ["PATH"]
    if stub_bin is not None:
        path = f"{stub_bin}:{path}"
    env = {
        **os.environ.copy(),
        "PATH": path,
        # Colors off so output assertions stay literal.
        "NO_COLOR": "1",
        **(extra_env or {}),
    }
    return subprocess.run(  # nosec B603 - fixed argv, shell=False
        [str(_SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


@pytest.fixture
def stub_bin(tmp_path: Path) -> Path:
    """Create the stub binary directory for a test.

    Args:
        tmp_path: Pytest temporary directory.

    Returns:
        Path: Directory stubs are written into.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    return bin_dir


# --- snapshot: platform routing -----------------------------------------------


def test_snapshot_linux_uses_vmstat_and_free(stub_bin: Path) -> None:
    """A Linux snapshot includes the vmstat and free sections (#1707)."""
    _write_stub(stub_bin, "uname", 'echo "Linux"')
    _write_stub(stub_bin, "vmstat", 'echo "VMSTAT_STUB_OUTPUT"')
    _write_stub(stub_bin, "free", 'echo "FREE_STUB_OUTPUT"')

    result = _run(["snapshot"], stub_bin=stub_bin)

    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("=== snapshot ")
    assert_that(result.stdout).contains("--- free -m ---")
    assert_that(result.stdout).contains("FREE_STUB_OUTPUT")
    assert_that(result.stdout).contains("--- vmstat ---")
    assert_that(result.stdout).contains("VMSTAT_STUB_OUTPUT")


def test_snapshot_linux_notes_missing_tools(stub_bin: Path, tmp_path: Path) -> None:
    """Missing vmstat/free degrade to a note instead of a failure.

    PATH is reduced to the stubs plus the few coreutils the script needs so
    vmstat/free are genuinely unresolvable — a Linux dev box or CI image that
    ships them must not flip this test red.
    """
    _write_stub(stub_bin, "uname", 'echo "Linux"')
    core_dir = tmp_path / "core"
    core_dir.mkdir()
    for tool in ("bash", "date", "dirname"):
        for base in ("/bin", "/usr/bin", "/usr/local/bin", "/opt/homebrew/bin"):
            candidate = Path(base) / tool
            if candidate.exists():
                (core_dir / tool).symlink_to(candidate)
                break

    result = _run(
        ["snapshot"],
        stub_bin=stub_bin,
        extra_env={
            "PATH": f"{stub_bin}:{core_dir}",
        },
    )

    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("--- free not available ---")
    assert_that(result.stdout).contains("--- vmstat not available ---")


def test_snapshot_linux_tolerates_failing_probes(stub_bin: Path) -> None:
    """A failing vmstat/free is noted and never fails the snapshot."""
    _write_stub(stub_bin, "uname", 'echo "Linux"')
    _write_stub(stub_bin, "vmstat", 'echo "boom" >&2\nexit 1')
    _write_stub(stub_bin, "free", 'echo "boom" >&2\nexit 1')

    result = _run(["snapshot"], stub_bin=stub_bin)

    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("free failed (ignored)")
    assert_that(result.stdout).contains("vmstat failed (ignored)")


def test_snapshot_darwin_uses_vm_stat(stub_bin: Path) -> None:
    """A macOS snapshot routes to vm_stat."""
    _write_stub(stub_bin, "uname", 'echo "Darwin"')
    _write_stub(stub_bin, "vm_stat", 'echo "VM_STAT_STUB_OUTPUT"')

    result = _run(["snapshot"], stub_bin=stub_bin)

    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("--- vm_stat ---")
    assert_that(result.stdout).contains("VM_STAT_STUB_OUTPUT")


def test_snapshot_unsupported_platform_is_noted(stub_bin: Path) -> None:
    """An unknown kernel is reported rather than crashing the sampler."""
    _write_stub(stub_bin, "uname", 'echo "SunOS"')

    result = _run(["snapshot"], stub_bin=stub_bin)

    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("--- unsupported platform: SunOS ---")


# --- start/stop lifecycle -------------------------------------------------------


def test_start_then_stop_records_snapshots_and_cleans_up(
    stub_bin: Path,
    tmp_path: Path,
) -> None:
    """Start backgrounds the sampler; stop ends it and brackets the log."""
    _write_stub(stub_bin, "uname", 'echo "Linux"')
    _write_stub(stub_bin, "vmstat", 'echo "VMSTAT_STUB_OUTPUT"')
    _write_stub(stub_bin, "free", 'echo "FREE_STUB_OUTPUT"')
    log_file = tmp_path / "memory-sampler.log"
    pid_file = tmp_path / "memory-sampler.pid"

    started = _run(
        ["start", str(log_file), str(pid_file), "1"],
        stub_bin=stub_bin,
    )
    assert_that(started.returncode).is_equal_to(0)
    assert_that(pid_file.is_file()).is_true()
    sampler_pid = int(pid_file.read_text().strip())

    try:
        # The sampler appends within ~1s; give it a bounded window. Poll for
        # the stub marker (not just the header) so a partially flushed first
        # snapshot cannot race the assertion.
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not (
            log_file.is_file() and "VMSTAT_STUB_OUTPUT" in log_file.read_text()
        ):
            time.sleep(0.1)
        assert_that(log_file.is_file()).is_true()
        assert_that(log_file.read_text()).contains("VMSTAT_STUB_OUTPUT")
    finally:
        stopped = _run(["stop", str(log_file), str(pid_file)], stub_bin=stub_bin)

    assert_that(stopped.returncode).is_equal_to(0)
    assert_that(pid_file.exists()).is_false()
    # The sampler process is really gone (not just the pid file).
    assert_that(
        subprocess.run(  # nosec B603 B607 - fixed kill argv
            ["kill", "-0", str(sampler_pid)],
            capture_output=True,
            check=False,
        ).returncode,
    ).is_not_equal_to(0)
    # stop brackets the log with a final snapshot and a stop marker.
    assert_that(log_file.read_text()).contains("=== sampler stopped ")


def test_start_reuses_live_sampler(stub_bin: Path, tmp_path: Path) -> None:
    """A second start against a live sampler is an idempotent no-op."""
    _write_stub(stub_bin, "uname", 'echo "Linux"')
    log_file = tmp_path / "memory-sampler.log"
    pid_file = tmp_path / "memory-sampler.pid"

    first = _run(["start", str(log_file), str(pid_file), "60"], stub_bin=stub_bin)
    assert_that(first.returncode).is_equal_to(0)
    first_pid = pid_file.read_text().strip()

    try:
        second = _run(["start", str(log_file), str(pid_file), "60"], stub_bin=stub_bin)
        assert_that(second.returncode).is_equal_to(0)
        assert_that(second.stdout + second.stderr).contains("already running")
        assert_that(pid_file.read_text().strip()).is_equal_to(first_pid)
    finally:
        _run(["stop", str(log_file), str(pid_file)], stub_bin=stub_bin)


def test_stop_without_pid_file_is_a_noop(stub_bin: Path, tmp_path: Path) -> None:
    """Stop tolerates a missing pid file (e.g. start never ran)."""
    result = _run(
        ["stop", str(tmp_path / "log"), str(tmp_path / "missing.pid")],
        stub_bin=stub_bin,
    )

    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout + result.stderr).contains("nothing to stop")


def test_stop_cleans_up_stale_pid_file(stub_bin: Path, tmp_path: Path) -> None:
    """A pid file pointing at a dead process is removed without failing."""
    log_file = tmp_path / "memory-sampler.log"
    pid_file = tmp_path / "memory-sampler.pid"
    log_file.write_text("")
    # Reapable-but-dead PID: start and exit a child, then record its PID.
    dead = subprocess.run(  # nosec B603 B607 - fixed argv
        ["true"],
        check=False,
    )
    del dead
    pid_file.write_text("999999")

    result = _run(["stop", str(log_file), str(pid_file)], stub_bin=stub_bin)

    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout + result.stderr).contains("not running")
    assert_that(pid_file.exists()).is_false()
    # Even the cleanup path appends the stop marker for log bracketing.
    assert_that(log_file.read_text()).contains("=== sampler stopped ")


# --- CLI contract -----------------------------------------------------------------


def test_unknown_command_fails(stub_bin: Path) -> None:
    """An unknown subcommand exits non-zero with usage on stderr."""
    result = _run(["bogus"], stub_bin=stub_bin)

    assert_that(result.returncode).is_equal_to(1)
    assert_that(result.stderr).contains("Usage: memory-sampler.sh")


def test_help_lists_all_commands(stub_bin: Path) -> None:
    """--help documents snapshot, start, and stop."""
    result = _run(["--help"], stub_bin=stub_bin)

    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("snapshot")
    assert_that(result.stdout).contains("start <log-file> <pid-file>")
    assert_that(result.stdout).contains("stop <log-file> <pid-file>")

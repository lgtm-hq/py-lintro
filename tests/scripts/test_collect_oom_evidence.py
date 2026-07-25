"""Tests for scripts/ci/collect-oom-evidence.sh.

These exercise the failure-only OOM evidence step wired into build-binary.yml
(#1707) using stub ``uname``/``dmesg``/``journalctl``/``log`` binaries on
PATH. The script must ALWAYS exit 0 — dmesg is commonly restricted on hosted
runners (kernel.dmesg_restrict) — so every test asserts the report content
and the zero exit code, including the fully-degraded paths.
"""

from __future__ import annotations

import os
import subprocess  # nosec B404 - drives the script under test with shell=False
from pathlib import Path

import pytest
from assertpy import assert_that

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPT = _REPO_ROOT / "scripts/ci/collect-oom-evidence.sh"

_OOM_LINE = "[  123.456] Out of memory: Killed process 4242 (nuitka) total-vm:9G"


def _write_stub(bin_dir: Path, name: str, body: str) -> None:
    """Write an executable stub binary into a PATH shim directory.

    Args:
        bin_dir: Directory prepended to PATH for the script under test.
        name: Binary name to shadow (e.g. ``dmesg``).
        body: Bash script body executed when the stub is invoked.
    """
    stub = bin_dir / name
    stub.write_text(f"#!/usr/bin/env bash\n{body}\n")
    stub.chmod(0o755)


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


def _run_collect(
    output_file: Path,
    *,
    stub_bin: Path,
    args: list[str] | None = None,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run collect-oom-evidence.sh with stub binaries on PATH.

    Args:
        output_file: Report file argument passed to the script.
        stub_bin: Directory of stub binaries to prepend to PATH.
        args: Full argument list overriding the default output-file form.
        extra_env: Extra environment variables for the script.

    Returns:
        subprocess.CompletedProcess[str]: The completed process.
    """
    env = {
        **os.environ.copy(),
        "PATH": f"{stub_bin}:{os.environ['PATH']}",
        "NO_COLOR": "1",
        **(extra_env or {}),
    }
    return subprocess.run(  # nosec B603 - fixed argv, shell=False
        [str(_SCRIPT), *(args if args is not None else [str(output_file)])],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_dmesg_oom_lines_are_captured(stub_bin: Path, tmp_path: Path) -> None:
    """Kernel-ring OOM kills land in the report (#1707)."""
    _write_stub(stub_bin, "uname", 'echo "Linux"')
    _write_stub(stub_bin, "dmesg", f'echo "boot noise"\necho "{_OOM_LINE}"')
    out = tmp_path / "oom-evidence.txt"

    result = _run_collect(out, stub_bin=stub_bin)

    assert_that(result.returncode).is_equal_to(0)
    report = out.read_text()
    assert_that(report).contains("=== OOM evidence collected ")
    assert_that(report).contains("--- dmesg OOM matches ---")
    assert_that(report).contains(_OOM_LINE)
    assert_that(report).does_not_contain("boot noise")


def test_clean_dmesg_reports_no_signatures(stub_bin: Path, tmp_path: Path) -> None:
    """A clean kernel log says so explicitly instead of going quiet."""
    _write_stub(stub_bin, "uname", 'echo "Linux"')
    _write_stub(stub_bin, "dmesg", 'echo "boot noise"')
    out = tmp_path / "oom-evidence.txt"

    result = _run_collect(out, stub_bin=stub_bin)

    assert_that(result.returncode).is_equal_to(0)
    assert_that(out.read_text()).contains(
        "(no OOM-killer signatures found in dmesg)",
    )


def test_restricted_dmesg_falls_back_to_journalctl(
    stub_bin: Path,
    tmp_path: Path,
) -> None:
    """Dmesg restrictions are noted and the journal fallback is used."""
    _write_stub(stub_bin, "uname", 'echo "Linux"')
    _write_stub(
        stub_bin,
        "dmesg",
        'echo "dmesg: read kernel buffer failed: Operation not permitted" >&2\nexit 1',
    )
    _write_stub(stub_bin, "journalctl", f'echo "{_OOM_LINE}"')
    out = tmp_path / "oom-evidence.txt"

    result = _run_collect(out, stub_bin=stub_bin)

    assert_that(result.returncode).is_equal_to(0)
    report = out.read_text()
    assert_that(report).contains("dmesg restricted or failed")
    assert_that(report).contains("Operation not permitted")
    assert_that(report).contains("--- journalctl -k fallback OOM matches ---")
    assert_that(report).contains(_OOM_LINE)


def test_restricted_dmesg_without_journalctl_still_exits_zero(
    stub_bin: Path,
    tmp_path: Path,
) -> None:
    """Fully degraded collection (no dmesg, no journal) never fails the job."""
    _write_stub(stub_bin, "uname", 'echo "Linux"')
    _write_stub(stub_bin, "dmesg", "exit 1")
    # No journalctl stub; it is absent from PATH on macOS runners locally.
    out = tmp_path / "oom-evidence.txt"

    result = _run_collect(out, stub_bin=stub_bin)

    assert_that(result.returncode).is_equal_to(0)
    report = out.read_text()
    assert_that(report).contains("dmesg restricted or failed")
    assert_that(result.stdout).contains("best-effort")


def test_missing_dmesg_uses_journalctl_when_present(
    stub_bin: Path,
    tmp_path: Path,
) -> None:
    """A host without dmesg at all still collects via the journal.

    macOS ships /sbin/dmesg, so "dmesg absent" cannot be simulated by
    omitting the stub; instead PATH is reduced to the stubs plus the few
    coreutils the script needs, leaving dmesg genuinely unresolvable.
    """
    _write_stub(stub_bin, "uname", 'echo "Linux"')
    _write_stub(stub_bin, "journalctl", f'echo "{_OOM_LINE}"')
    core_dir = tmp_path / "core"
    core_dir.mkdir()
    for tool in ("bash", "date", "grep", "cat"):
        for base in ("/bin", "/usr/bin", "/usr/local/bin", "/opt/homebrew/bin"):
            candidate = Path(base) / tool
            if candidate.exists():
                (core_dir / tool).symlink_to(candidate)
                break
    out = tmp_path / "oom-evidence.txt"

    env = {
        **os.environ.copy(),
        "PATH": f"{stub_bin}:{core_dir}",
        "NO_COLOR": "1",
    }
    result = subprocess.run(  # nosec B603 - fixed argv, shell=False
        [str(_SCRIPT), str(out)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert_that(result.returncode).is_equal_to(0)
    report = out.read_text()
    assert_that(report).contains("--- dmesg not available on PATH ---")
    assert_that(report).contains(_OOM_LINE)


def test_darwin_checks_jetsam_events(stub_bin: Path, tmp_path: Path) -> None:
    """MacOS routes to the unified log's memorystatus (Jetsam) events."""
    _write_stub(stub_bin, "uname", 'echo "Darwin"')
    _write_stub(
        stub_bin,
        "log",
        'echo "memorystatus: killing process 4242"',
    )
    out = tmp_path / "oom-evidence.txt"

    result = _run_collect(out, stub_bin=stub_bin)

    assert_that(result.returncode).is_equal_to(0)
    report = out.read_text()
    assert_that(report).contains("checking Jetsam events")
    assert_that(report).contains("memorystatus: killing process 4242")


def test_darwin_log_show_is_wall_clock_bounded(stub_bin: Path, tmp_path: Path) -> None:
    """A hung log show is killed instead of eating the failure-path budget.

    The step runs if: failure() inside a job whose timeout the compile has
    mostly consumed, so the unified-log query is bounded (Greptile on #1707).
    """
    _write_stub(stub_bin, "uname", 'echo "Darwin"')
    _write_stub(stub_bin, "log", "sleep 120")
    out = tmp_path / "oom-evidence.txt"

    result = _run_collect(
        out,
        stub_bin=stub_bin,
        extra_env={"OOM_LOG_SHOW_TIMEOUT": "1"},
    )

    assert_that(result.returncode).is_equal_to(0)
    assert_that(out.read_text()).contains("(log show timed out after 1s; skipped)")


def test_unsupported_platform_still_exits_zero(
    stub_bin: Path,
    tmp_path: Path,
) -> None:
    """An unknown kernel yields a note, never a failure."""
    _write_stub(stub_bin, "uname", 'echo "SunOS"')
    out = tmp_path / "oom-evidence.txt"

    result = _run_collect(out, stub_bin=stub_bin)

    assert_that(result.returncode).is_equal_to(0)
    assert_that(out.read_text()).contains("unsupported platform")


def test_help_exits_zero(stub_bin: Path, tmp_path: Path) -> None:
    """--help documents the output-file argument."""
    result = _run_collect(tmp_path / "unused.txt", stub_bin=stub_bin, args=["--help"])

    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("Usage: collect-oom-evidence.sh <output-file>")

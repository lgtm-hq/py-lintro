"""Tests for vuln-suppression-check security helper scripts."""

from __future__ import annotations

import os
import stat
import subprocess  # nosec B404 - subprocess is used to drive the tool/CLI under test; invocations use shell=False
import textwrap
from pathlib import Path

import pytest
from assertpy import assert_that

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_INSTALL_SCRIPT = _REPO_ROOT / "scripts/ci/security/install-osv-scanner.sh"
_CHECK_SCRIPT = _REPO_ROOT / "scripts/ci/security/check-vuln-suppressions.sh"
_LGTM_CI_PIN = "66cad82ead0e5d119928c895c7d7da9c837989e5"


@pytest.mark.parametrize(
    "script",
    [
        "scripts/ci/security/install-osv-scanner.sh",
        "scripts/ci/security/check-vuln-suppressions.sh",
    ],
)
def test_vuln_suppression_scripts_expose_help(script: str) -> None:
    """Each vuln-suppression helper should support --help."""
    script_path = (_REPO_ROOT / script).resolve()
    result = subprocess.run(  # nosec B603 - fixed argv run against a real binary in a controlled test; shell=False, no user shell input
        [str(script_path), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("Usage:")


@pytest.mark.parametrize(
    "script",
    [
        "scripts/ci/security/install-osv-scanner.sh",
        "scripts/ci/security/check-vuln-suppressions.sh",
    ],
)
def test_vuln_suppression_scripts_pass_syntax_check(script: str) -> None:
    """Each vuln-suppression helper should pass bash -n."""
    script_path = (_REPO_ROOT / script).resolve()
    result = subprocess.run(  # nosec B603 B607 - fixed argv run against a real binary in a controlled test; binary name resolved from PATH, not attacker-controlled; shell=False, no user shell input
        ["bash", "-n", str(script_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stderr).is_empty()


def test_install_script_reports_missing_tooling(tmp_path: Path) -> None:
    """install-osv-scanner.sh should fail clearly without lgtm-ci libs."""
    workspace = tmp_path / "repo"
    workspace.mkdir()
    script_copy = workspace / "install-osv-scanner.sh"
    script_copy.write_text(_INSTALL_SCRIPT.read_text())
    script_copy.chmod(script_copy.stat().st_mode | stat.S_IXUSR)

    result = subprocess.run(  # nosec B603 - fixed argv run against a real binary in a controlled test; shell=False, no user shell input
        [str(script_copy)],
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ.copy(),
            "GITHUB_WORKSPACE": str(workspace),
        },
    )

    assert_that(result.returncode).is_equal_to(1)
    assert_that(result.stderr).contains("lgtm-ci tooling not found")


def test_check_script_reports_missing_tooling(tmp_path: Path) -> None:
    """check-vuln-suppressions.sh should fail clearly without tooling script."""
    workspace = tmp_path / "repo"
    workspace.mkdir()
    script_copy = workspace / "check-vuln-suppressions.sh"
    script_copy.write_text(_CHECK_SCRIPT.read_text())
    script_copy.chmod(script_copy.stat().st_mode | stat.S_IXUSR)

    result = subprocess.run(  # nosec B603 - fixed argv run against a real binary in a controlled test; shell=False, no user shell input
        [str(script_copy)],
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ.copy(),
            "GITHUB_WORKSPACE": str(workspace),
            "GH_TOKEN": "fake-token",
        },
    )

    assert_that(result.returncode).is_equal_to(1)
    assert_that(result.stderr).contains("Missing lgtm-ci tooling script")


def test_install_script_retries_transient_curl_exit_23(tmp_path: Path) -> None:
    """Transient curl exit 23 should be retried before surfacing diagnostics."""
    workspace = tmp_path / "repo"
    tooling_lib = workspace / ".lgtm-ci-tooling/scripts/ci/lib"
    network_lib = tooling_lib / "network"
    network_lib.mkdir(parents=True)

    (tooling_lib / "log.sh").write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            log_info() { echo "[INFO] $*" >&2; }
            log_success() { echo "[SUCCESS] $*" >&2; }
            log_warn() { echo "[WARN] $*" >&2; }
            log_error() { echo "[ERROR] $*" >&2; }
            log_verbose() { :; }
            """,
        ),
    )
    (tooling_lib / "fs.sh").write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            # shellcheck source=log.sh
            source "$(dirname "${BASH_SOURCE[0]}")/log.sh"
            """,
        ),
    )
    (network_lib / "download.sh").write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            # shellcheck source=../log.sh
            source "$(dirname "${BASH_SOURCE[0]}")/../log.sh"
            _lgtm_ci_build_curl_args() { _LGTM_CI_CURL_ARGS=(-fsSL); return 0; }
            download_with_retries() {
              local url="$1"
              local out="$2"
              local attempts="${3:-3}"
              local i
              for ((i = 1; i <= attempts; i++)); do
                if curl "${_LGTM_CI_CURL_ARGS[@]}" "$url" -o "$out"; then
                  return 0
                fi
              done
              return 1
            }
            """,
        ),
    )

    mock_bin = tmp_path / "bin"
    mock_bin.mkdir()
    attempts_file = tmp_path / "curl-attempts"
    uname_mock = mock_bin / "uname"
    uname_mock.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            if [[ "${1:-}" == "-s" ]]; then
              echo Linux
              exit 0
            fi
            if [[ "${1:-}" == "-m" ]]; then
              echo x86_64
              exit 0
            fi
            exit 1
            """,
        ),
    )
    uname_mock.chmod(uname_mock.stat().st_mode | stat.S_IXUSR)
    curl_mock = mock_bin / "curl"
    curl_mock.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            set -euo pipefail
            attempts=0
            if [[ -f "{attempts_file}" ]]; then
              attempts=$(cat "{attempts_file}")
            fi
            echo $((attempts + 1)) > "{attempts_file}"
            echo "curl: (23) Failure writing output to destination" >&2
            exit 23
            """,
        ),
    )
    curl_mock.chmod(curl_mock.stat().st_mode | stat.S_IXUSR)

    script_copy = workspace / "install-osv-scanner.sh"
    script_copy.write_text(_INSTALL_SCRIPT.read_text())
    script_copy.chmod(script_copy.stat().st_mode | stat.S_IXUSR)

    env = os.environ.copy()
    env["GITHUB_WORKSPACE"] = str(workspace)
    env["PATH"] = f"{mock_bin}:{env['PATH']}"
    env["DOWNLOAD_MAX_ATTEMPTS"] = "3"

    result = subprocess.run(  # nosec B603 - fixed argv run against a real binary in a controlled test; shell=False, no user shell input
        ["bash", str(script_copy), "9.9.9"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert_that(result.returncode).is_equal_to(1)
    assert_that(int(attempts_file.read_text().strip())).is_greater_than_or_equal_to(3)
    assert_that(result.stderr).contains("curl exit 23")


def test_vuln_suppression_workflow_uses_local_scripts() -> None:
    """vuln-suppression-check.yml should wire local install/check scripts."""
    workflow_path = _REPO_ROOT / ".github/workflows/vuln-suppression-check.yml"
    content = workflow_path.read_text(encoding="utf-8")

    assert_that(content).contains(
        "install-script: scripts/ci/security/install-osv-scanner.sh"
    )
    assert_that(content).contains(
        "check-script: scripts/ci/security/check-vuln-suppressions.sh"
    )
    assert_that(content).contains(_LGTM_CI_PIN)

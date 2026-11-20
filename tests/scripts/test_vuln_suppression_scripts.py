"""Tests for vuln-suppression-check security helper scripts."""

from __future__ import annotations

import os
import re
import stat
import subprocess  # nosec B404 - subprocess is used to drive the tool/CLI under test; invocations use shell=False
import textwrap
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro._tool_versions import get_tool_version
from lintro.enums.tool_name import ToolName

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_INSTALL_SCRIPT = _REPO_ROOT / "scripts/ci/security/install-osv-scanner.sh"
_CHECK_SCRIPT = _REPO_ROOT / "scripts/ci/security/check-vuln-suppressions.sh"
_WORKFLOW_PATH = _REPO_ROOT / ".github/workflows/vuln-suppression-check.yml"
_USES_PIN_RE = re.compile(
    r"reusable-vuln-suppression-check\.yml@([0-9a-f]{40})",
)
_TOOLING_REF_RE = re.compile(
    r"tooling-ref:\s*[\"']?([0-9a-f]{40})[\"']?",
)


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
            "GH_TOKEN": "fake-token",  # nosec B105 - test-only placeholder, not a real credential
        },
    )

    assert_that(result.returncode).is_equal_to(1)
    assert_that(result.stderr).contains("Missing lgtm-ci tooling script")


def _install_fake_tooling(workspace: Path) -> None:
    """Write minimal lgtm-ci log/fs/download libraries into a workspace.

    Args:
        workspace: Directory standing in for ``GITHUB_WORKSPACE``.
    """
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


def _mock_linux_bin(tmp_path: Path, curl_script: str) -> Path:
    """Create a bin directory with ``uname`` reporting Linux and a fake ``curl``.

    Args:
        tmp_path: Directory to create ``bin`` in.
        curl_script: Body of the fake ``curl`` executable.

    Returns:
        The bin directory, to put first on ``PATH``.
    """
    mock_bin = tmp_path / "bin"
    mock_bin.mkdir()
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
    curl_mock.write_text(curl_script)
    curl_mock.chmod(curl_mock.stat().st_mode | stat.S_IXUSR)
    return mock_bin


def _copy_install_script(workspace: Path) -> Path:
    """Copy the installer into a workspace and make it executable.

    Args:
        workspace: Directory standing in for ``GITHUB_WORKSPACE``.

    Returns:
        Path to the copied script.
    """
    script_copy = workspace / "install-osv-scanner.sh"
    script_copy.write_text(_INSTALL_SCRIPT.read_text())
    script_copy.chmod(script_copy.stat().st_mode | stat.S_IXUSR)
    return script_copy


def test_install_script_retries_transient_curl_exit_23(tmp_path: Path) -> None:
    """Transient curl exit 23 should be retried before surfacing diagnostics."""
    workspace = tmp_path / "repo"
    _install_fake_tooling(workspace)
    attempts_file = tmp_path / "curl-attempts"
    mock_bin = _mock_linux_bin(
        tmp_path,
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
    script_copy = _copy_install_script(workspace)

    env = os.environ.copy()
    env["GITHUB_WORKSPACE"] = str(workspace)
    env["PATH"] = f"{mock_bin}:{env['PATH']}"
    env["DOWNLOAD_MAX_ATTEMPTS"] = "3"

    result = subprocess.run(  # nosec B603 B607 - fixed argv run against a real binary in a controlled test; bash resolved from PATH in controlled env; shell=False, no user shell input
        ["bash", str(script_copy), "9.9.9"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert_that(result.returncode).is_equal_to(1)
    assert_that(int(attempts_file.read_text().strip())).is_greater_than_or_equal_to(3)
    assert_that(result.stderr).contains("curl exit 23")


def _run_install_without_version(
    tmp_path: Path,
    *,
    with_pin_file: bool,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    """Run the installer with no version argument and no ``OSV_VERSION``.

    The fake ``curl`` records each URL and fails, so nothing is installed.

    Args:
        tmp_path: Pytest temporary directory.
        with_pin_file: Copy the real ``lintro/_tool_versions.py`` into the
            workspace, as the CI checkout has it.

    Returns:
        The finished process and the file holding the requested URLs.
    """
    workspace = tmp_path / "repo"
    _install_fake_tooling(workspace)
    if with_pin_file:
        (workspace / "lintro").mkdir()
        (workspace / "lintro/_tool_versions.py").write_text(
            (_REPO_ROOT / "lintro/_tool_versions.py").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    urls_file = tmp_path / "curl-urls"
    urls_file.touch()
    mock_bin = _mock_linux_bin(
        tmp_path,
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            for arg in "$@"; do
              case "$arg" in https://*) echo "$arg" >> "{urls_file}" ;; esac
            done
            exit 22
            """,
        ),
    )
    script_copy = _copy_install_script(workspace)

    env = os.environ.copy()
    env.pop("OSV_VERSION", None)
    env["GITHUB_WORKSPACE"] = str(workspace)
    env["PATH"] = f"{mock_bin}:{env['PATH']}"
    env["DOWNLOAD_MAX_ATTEMPTS"] = "1"

    # The reusable workflow passes its empty ``osv-version`` input as $1.
    result = subprocess.run(  # nosec B603 B607 - fixed argv run against a real binary in a controlled test; bash resolved from PATH in controlled env; shell=False, no user shell input
        ["bash", str(script_copy), ""],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    return result, urls_file


def test_install_script_defaults_to_the_shipped_pin(tmp_path: Path) -> None:
    """With no version given, CI installs the osv-scanner lintro pins (#3557).

    The suppression-check workflow passes no version, so this default is the
    osv-scanner CI runs. It is read from ``_tool_versions.py``, the pin the
    tools image installs and lintro's ``min_version`` equals, so it cannot
    fall behind the floor when Renovate bumps the pin.
    """
    result, urls_file = _run_install_without_version(tmp_path, with_pin_file=True)
    pin = get_tool_version(ToolName.OSV_SCANNER)

    assert_that(result.stderr).contains(f"Installing osv-scanner v{pin} ")
    assert_that(urls_file.read_text().splitlines()[0]).ends_with(
        f"/releases/download/v{pin}/osv-scanner_linux_amd64",
    )


def test_install_script_without_pin_fails_before_downloading(
    tmp_path: Path,
) -> None:
    """A checkout without the pin file stops with a named error, no download."""
    result, urls_file = _run_install_without_version(tmp_path, with_pin_file=False)

    assert_that(result.returncode).is_equal_to(1)
    assert_that(result.stderr).contains(
        "no ToolName.OSV_SCANNER pin found in",
    )
    assert_that(urls_file.read_text()).is_empty()


def test_vuln_suppression_workflow_uses_local_scripts() -> None:
    """vuln-suppression-check.yml should wire local install/check scripts."""
    content = _WORKFLOW_PATH.read_text(encoding="utf-8")

    assert_that(content).contains(
        "install-script: scripts/ci/security/install-osv-scanner.sh",
    )
    assert_that(content).contains(
        "check-script: scripts/ci/security/check-vuln-suppressions.sh",
    )

    uses_match = _USES_PIN_RE.search(content)
    tooling_match = _TOOLING_REF_RE.search(content)
    assert_that(uses_match).is_not_none()
    assert_that(tooling_match).is_not_none()
    assert uses_match is not None  # narrow type for mypy
    assert tooling_match is not None  # narrow type for mypy
    assert_that(uses_match.group(1)).is_equal_to(tooling_match.group(1))

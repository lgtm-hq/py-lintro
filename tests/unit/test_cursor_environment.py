"""Contract tests for the Cloud Agent environment bootstrap files."""

from __future__ import annotations

import json
import stat
import subprocess  # nosec B404 - drives install.sh with a fixed argv and shell=False
from pathlib import Path

from assertpy import assert_that

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ENVIRONMENT_JSON = _REPO_ROOT / ".cursor" / "environment.json"
_INSTALL_SH = _REPO_ROOT / ".cursor" / "install.sh"
_EXPECTED_NAME = "lintro (Python CLI)"


def _uncommented_lines(*, text: str) -> list[str]:
    """Return non-empty source lines with comments stripped.

    Args:
        text: Full script contents.

    Returns:
        Lines that would execute, without trailing comments.
    """
    lines: list[str] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        code, _sep, _comment = stripped.partition(" #")
        lines.append(code.rstrip())
    return lines


def _write_executable(*, path: Path, body: str) -> None:
    """Write a bash helper and mark it executable.

    Args:
        path: Destination path.
        body: Script contents.
    """
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _fake_uv(*, path: Path, log: Path, sync_exit: int) -> None:
    """Install a recording ``uv`` stub.

    Args:
        path: Path of the stub executable.
        log: File that receives argv lines.
        sync_exit: Exit code for non-``--version`` invocations.
    """
    _write_executable(
        path=path,
        body=(
            "#!/usr/bin/env bash\n"
            'if [ "$1" = "--version" ]; then\n'
            "  echo 'uv 0.0.0-test'\n"
            "  exit 0\n"
            "fi\n"
            f"printf 'UV_LINK_MODE=%s %s\\n' \"${{UV_LINK_MODE-}}\" \"$*\" >> '{log}'\n"
            f"exit {sync_exit}\n"
        ),
    )


def _run_install(
    *,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    """Run ``.cursor/install.sh`` with a controlled environment.

    Args:
        env: Complete environment for the child process.

    Returns:
        The completed subprocess result.
    """
    return subprocess.run(  # nosec B603 - fixed argv, no shell
        ["bash", str(_INSTALL_SH)],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
        env=env,
        check=False,
    )


def test_environment_json_is_valid_and_points_at_install_script() -> None:
    """Cloud Agent environment.json must name ubuntu and invoke install.sh."""
    payload = json.loads(_ENVIRONMENT_JSON.read_text(encoding="utf-8"))

    assert_that(payload["name"]).is_equal_to(_EXPECTED_NAME)
    assert_that(payload["user"]).is_equal_to("ubuntu")
    assert_that(payload["install"]).is_equal_to("bash .cursor/install.sh")
    assert_that(_INSTALL_SH.exists()).is_true()


def test_install_script_is_strict_bash_and_syncs_full_extra() -> None:
    """The install script must parse, fail closed, and sync the full extra."""
    text = _INSTALL_SH.read_text(encoding="utf-8")
    lines = _uncommented_lines(text=text)
    syntax = subprocess.run(  # nosec B603 - fixed argv, no shell
        ["bash", "-n", str(_INSTALL_SH)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert_that(text).starts_with("#!/usr/bin/env bash")
    assert_that(syntax.returncode).is_equal_to(0)
    assert_that(lines).contains("set -euo pipefail")
    assert_that(lines).contains("export UV_LINK_MODE=copy")
    assert_that(lines).contains("uv sync --dev --extra full")
    assert_that(_INSTALL_SH.stat().st_mode & stat.S_IXUSR).is_not_equal_to(0)


def test_install_script_uses_existing_uv_without_curl(
    tmp_path: Path,
) -> None:
    """When uv is already on PATH, install.sh must sync and never call curl.

    Args:
        tmp_path: Isolated HOME and fake PATH for the child process.
    """
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    log = tmp_path / "uv-args.log"
    curl_log = tmp_path / "curl.log"
    _fake_uv(path=fake_bin / "uv", log=log, sync_exit=0)
    _write_executable(
        path=fake_bin / "curl",
        body=(f"#!/usr/bin/env bash\nprintf '%s\\n' \"$*\" >> '{curl_log}'\nexit 1\n"),
    )

    result = _run_install(
        env={
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "HOME": str(home),
        },
    )

    assert_that(result.returncode).is_equal_to(0)
    recorded = log.read_text(encoding="utf-8")
    assert_that(recorded).contains("UV_LINK_MODE=copy")
    assert_that(recorded).contains("sync --dev --extra full")
    assert_that(curl_log.exists()).is_false()


def test_install_script_fails_when_uv_sync_fails(
    tmp_path: Path,
) -> None:
    """A failing ``uv sync`` must fail the install script.

    Args:
        tmp_path: Isolated HOME and fake PATH for the child process.
    """
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    log = tmp_path / "uv-args.log"
    _fake_uv(path=fake_bin / "uv", log=log, sync_exit=2)

    result = _run_install(
        env={
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "HOME": str(home),
        },
    )

    assert_that(result.returncode).is_equal_to(2)
    assert_that(log.read_text(encoding="utf-8")).contains(
        "sync --dev --extra full",
    )


def test_install_script_bootstraps_uv_via_curl_when_missing(
    tmp_path: Path,
) -> None:
    """When uv is absent, install.sh must run the official installer then sync.

    Args:
        tmp_path: Isolated HOME and fake PATH for the child process.
    """
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    home = tmp_path / "home"
    local_bin = home / ".local" / "bin"
    local_bin.mkdir(parents=True)
    log = tmp_path / "uv-args.log"
    curl_log = tmp_path / "curl.log"
    installed_uv = local_bin / "uv"
    _write_executable(
        path=fake_bin / "curl",
        body=(
            "#!/usr/bin/env bash\n"
            f"printf '%s\\n' \"$*\" >> '{curl_log}'\n"
            "cat <<'EOS'\n"
            "#!/usr/bin/env bash\n"
            f"mkdir -p '{local_bin}'\n"
            f"cp '{fake_bin / 'bootstrap-uv'}' '{installed_uv}'\n"
            f"chmod +x '{installed_uv}'\n"
            "EOS\n"
        ),
    )
    _fake_uv(path=fake_bin / "bootstrap-uv", log=log, sync_exit=0)

    result = _run_install(
        env={
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "HOME": str(home),
        },
    )

    assert_that(result.returncode).is_equal_to(0)
    assert_that(curl_log.read_text(encoding="utf-8")).contains(
        "https://astral.sh/uv/install.sh",
    )
    assert_that(log.read_text(encoding="utf-8")).contains(
        "sync --dev --extra full",
    )

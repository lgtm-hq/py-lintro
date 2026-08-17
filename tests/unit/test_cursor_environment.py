"""Contract tests for the Cloud Agent environment bootstrap files."""

from __future__ import annotations

import json
import os
import stat
import subprocess  # nosec B404 - drives install.sh with a fixed argv and shell=False
from pathlib import Path

from assertpy import assert_that

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ENVIRONMENT_JSON = _REPO_ROOT / ".cursor" / "environment.json"
_INSTALL_SH = _REPO_ROOT / ".cursor" / "install.sh"


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

    assert_that(payload).contains_key("name")
    assert_that(payload).contains_key("user")
    assert_that(payload["user"]).is_equal_to("ubuntu")
    assert_that(payload).contains_key("install")
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
    uv_script = fake_bin / "uv"
    uv_script.write_text(
        (
            "#!/usr/bin/env bash\n"
            'if [ "$1" = "--version" ]; then\n'
            "  echo 'uv 0.0.0-test'\n"
            "  exit 0\n"
            "fi\n"
            f"printf '%s\\n' \"$*\" >> '{log}'\n"
            "exit 0\n"
        ),
        encoding="utf-8",
    )
    uv_script.chmod(uv_script.stat().st_mode | stat.S_IXUSR)
    curl_script = fake_bin / "curl"
    curl_script.write_text(
        (
            "#!/usr/bin/env bash\n"
            f"printf '%s\\n' \"$*\" >> '{curl_log}'\n"
            "exit 1\n"
        ),
        encoding="utf-8",
    )
    curl_script.chmod(curl_script.stat().st_mode | stat.S_IXUSR)

    result = _run_install(
        env={
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "HOME": str(home),
            "UV_LINK_MODE": "copy",
        },
    )

    assert_that(result.returncode).is_equal_to(0)
    assert_that(log.read_text(encoding="utf-8")).contains(
        "sync --dev --extra full",
    )
    assert_that(curl_log.exists()).is_false()

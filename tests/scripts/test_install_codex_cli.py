"""Tests for ``scripts/ci/install-codex-cli.sh``.

The dogfood job's openai lane (#2472) installs the pinned OpenAI ``codex``
CLI onto a bare runner. These tests cover the script's local validation —
missing pins, malformed versions — so a bad caller fails before it hits the
network. The install itself is exercised in CI, not here.
"""

from __future__ import annotations

import os
import subprocess  # nosec B404 - subprocess is used to drive the script under test; invocations use shell=False
from pathlib import Path

import pytest
from assertpy import assert_that

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "ci" / "install-codex-cli.sh"

VALID_VERSION = "0.147.0"


def _run(
    *,
    env_overrides: dict[str, str],
    args: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the installer with a controlled environment.

    Args:
        env_overrides: Environment variables layered onto a minimal base.
        args: Optional positional arguments passed to the script.

    Returns:
        The completed subprocess result.
    """
    env = {"PATH": os.environ.get("PATH", "")}
    env.update(env_overrides)
    return subprocess.run(  # nosec B603 - fixed argv run against a real binary; shell=False
        [str(SCRIPT), *(args or [])],
        capture_output=True,
        text=True,
        env=env,
    )


def test_help_exits_zero() -> None:
    """The --help flag prints usage and exits 0."""
    result = _run(env_overrides={}, args=["--help"])

    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("Usage:")
    assert_that(result.stdout).contains("CODEX_VERSION")


def test_missing_version_fails() -> None:
    """An unset CODEX_VERSION is a hard error, not a silent default."""
    result = _run(env_overrides={})

    assert_that(result.returncode).is_not_equal_to(0)
    assert_that(result.stderr).contains("CODEX_VERSION is required")


@pytest.mark.parametrize("bad_version", ["latest", "^0.147.0", "0.147", "v0.147.0"])
def test_malformed_version_fails(bad_version: str) -> None:
    """Only exact X.Y.Z versions install — no ranges, tags, or aliases.

    `latest` and npm aliases are non-empty, so an emptiness check alone would
    let the installed binary move between runs in a job that holds a
    credential.
    """
    result = _run(env_overrides={"CODEX_VERSION": bad_version})

    assert_that(result.returncode).is_not_equal_to(0)
    assert_that(result.stderr).contains("exact X.Y.Z")


def test_script_pins_the_openai_package_and_verifies_binary() -> None:
    """The install targets @openai/codex at the pinned version, then verifies.

    The npm package and the post-install `codex --version` probe are the
    contract the workflow and lintro's capability gate rely on.
    """
    script = SCRIPT.read_text(encoding="utf-8")

    assert_that(script).contains(
        'npm install -g --no-fund --no-audit "@openai/codex@${CODEX_VERSION}"',
    )
    assert_that(script).contains("codex --version")


def test_well_formed_pin_passes_validation(tmp_path: Path) -> None:
    """A well-formed X.Y.Z pin passes the regex and drives the install.

    Only rejections are covered above, so a regex tightened one character
    too far would reject every real pin unnoticed. Stub ``npm``/``codex``
    binaries stand in for the network: the install line must carry the
    pinned package and the ``codex --version`` probe must run.
    """
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    npm_log = tmp_path / "npm-args.log"
    npm_stub = stub_dir / "npm"
    npm_stub.write_text(
        "#!/usr/bin/env bash\n" f'printf \'%s\\n\' "$@" >> "{npm_log}"\n',
        encoding="utf-8",
    )
    codex_stub = stub_dir / "codex"
    codex_stub.write_text(
        "#!/usr/bin/env bash\n"
        'if [ "$1" = "--version" ]; then echo "' + VALID_VERSION + '"; fi\n',
        encoding="utf-8",
    )
    npm_stub.chmod(0o755)
    codex_stub.chmod(0o755)

    result = _run(
        env_overrides={
            "CODEX_VERSION": VALID_VERSION,
            "PATH": f"{stub_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        },
    )

    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains(
        f"Installing @openai/codex@{VALID_VERSION}...",
    )
    assert_that(npm_log.read_text(encoding="utf-8")).contains(
        f"@openai/codex@{VALID_VERSION}",
    )

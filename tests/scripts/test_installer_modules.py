"""Tests for modular install-tools group installers under scripts/utils/installers/."""

from __future__ import annotations

import os
import subprocess  # nosec B404 - drives installer scripts with fixed argv
from pathlib import Path

import pytest
from assertpy import assert_that

_REPO_ROOT = Path(__file__).resolve().parents[2]
_INSTALLERS = _REPO_ROOT / "scripts" / "utils" / "installers"
_ORCHESTRATOR = _REPO_ROOT / "scripts" / "utils" / "install-tools.sh"
_TOOLS_PUBLISH = _REPO_ROOT / ".github" / "workflows" / "docker-tools-publish.yml"

_GROUP_INSTALLERS = (
    "binary-tools.sh",
    "node-tools.sh",
    "python-tools.sh",
    "rust-tools.sh",
)


def _run(
    script: Path,
    *,
    args: list[str] | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run an installer script with a controlled environment.

    Args:
        script: Path to the bash installer entrypoint.
        args: Optional CLI arguments.
        env: Optional environment overrides.

    Returns:
        The completed subprocess result.
    """
    base_env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
    }
    if env:
        base_env.update(env)
    return subprocess.run(  # nosec B603 - fixed argv, shell=False
        [str(script), *(args or [])],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=base_env,
    )


@pytest.mark.parametrize("name", _GROUP_INSTALLERS)
def test_group_installer_files_exist_and_are_executable(name: str) -> None:
    """Each group installer module must exist and be executable."""
    path = _INSTALLERS / name
    assert_that(path.is_file()).is_true()
    assert_that(path.stat().st_mode & 0o111).is_not_equal_to(0)


def test_helpers_module_exists() -> None:
    """Shared helpers must be present for orchestrator and group scripts."""
    path = _INSTALLERS / "_helpers.sh"
    assert_that(path.is_file()).is_true()


@pytest.mark.parametrize("name", ("_helpers.sh", *_GROUP_INSTALLERS))
def test_installer_bash_syntax(name: str) -> None:
    """Installer bash sources must pass bash -n syntax checking."""
    path = _INSTALLERS / name
    result = subprocess.run(  # nosec B603 - fixed argv, shell=False
        ["bash", "-n", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stderr).is_empty()


def test_orchestrator_sources_all_group_installers() -> None:
    """install-tools.sh must source every group installer module."""
    text = _ORCHESTRATOR.read_text(encoding="utf-8")
    for name in _GROUP_INSTALLERS:
        assert_that(text).contains(f"installers/{name}")
    assert_that(text).contains("install_rust_tools")
    assert_that(text).contains("install_binary_tools")
    assert_that(text).contains("install_python_tools")
    assert_that(text).contains("install_node_tools")


def test_docker_tools_publish_watches_installer_modules() -> None:
    """Tools image rebuild must trigger when installer modules change."""
    text = _TOOLS_PUBLISH.read_text(encoding="utf-8")
    assert_that(text).contains("scripts/utils/installers/**")


@pytest.mark.parametrize("name", _GROUP_INSTALLERS)
def test_group_installer_help_exits_zero(name: str) -> None:
    """Direct --help on group installers documents usage and exits 0."""
    result = _run(_INSTALLERS / name, args=["--help"])
    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("Usage:")
    assert_that(result.stdout).contains("--dry-run")


@pytest.mark.parametrize("name", _GROUP_INSTALLERS)
def test_group_installer_dry_run_exits_zero(name: str) -> None:
    """--dry-run on group installers must not mutate the system."""
    result = _run(_INSTALLERS / name, args=["--dry-run", "--local"])
    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout + result.stderr).contains("[DRY-RUN]")


def test_group_installer_unknown_flag_fails_fast() -> None:
    """Unknown CLI flags must error instead of being silently ignored."""
    result = _run(_INSTALLERS / "python-tools.sh", args=["--typo-flag"])
    assert_that(result.returncode).is_not_equal_to(0)
    assert_that(result.stderr).contains("unknown option")


def test_python_tools_sets_script_dir_for_semgrep() -> None:
    """Direct python-tools invocation must reference install-semgrep.sh correctly."""
    text = (_INSTALLERS / "python-tools.sh").read_text(encoding="utf-8")
    assert_that(text).contains('SCRIPT_DIR:=$(cd "$_PYTHON_TOOLS_DIR/.." && pwd)')
    assert_that(text).contains("$SCRIPT_DIR/install-semgrep.sh")


def test_helpers_defer_bin_dir_to_ensure_bin_dir() -> None:
    """BIN_DIR must not be pinned to ~/.local/bin at _helpers.sh source time."""
    text = (_INSTALLERS / "_helpers.sh").read_text(encoding="utf-8")
    defaults_block = text.split("# Defaults for globals", maxsplit=1)[1].split(
        "get_tool_version()",
        maxsplit=1,
    )[0]
    assert_that(defaults_block).does_not_contain('BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"')
    assert_that(text).contains("parse_group_installer_args")
    assert_that(text).contains("ensure_bin_dir()")


def test_hadolint_checksum_is_fail_closed() -> None:
    """Hadolint install must refuse to proceed without checksum verification."""
    text = (_INSTALLERS / "_helpers.sh").read_text(encoding="utf-8")
    assert_that(text).contains("refusing to install $tool_name")
    assert_that(text).does_not_contain("skipping verification")


def test_rust_clippy_uses_toolchain_helper() -> None:
    """Clippy install must route through ensure_rust_toolchain for version pins."""
    text = (_INSTALLERS / "rust-tools.sh").read_text(encoding="utf-8")
    install_clippy = text.split("install_clippy() {", maxsplit=1)[1].split("\n}\n", maxsplit=1)[0]
    assert_that(install_clippy).contains('ensure_rust_toolchain "clippy"')
    assert_that(install_clippy).does_not_contain("cargo clippy --version")


def test_cargo_tools_fail_when_installation_unavailable() -> None:
    """Requested cargo-audit/cargo-deny installs must exit non-zero on failure."""
    text = (_INSTALLERS / "rust-tools.sh").read_text(encoding="utf-8")
    assert_that(text).contains("Failed to install cargo-audit")
    assert_that(text).contains("Failed to install cargo-deny")
    assert_that(text).does_not_contain("optional tool")

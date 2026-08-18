"""Tests that semgrep is isolated from lintro's shared Python resolver."""

from __future__ import annotations

import json
import re
import subprocess  # nosec B404 - drives install-semgrep.sh with a fixed argv
import tomllib
from pathlib import Path

from assertpy import assert_that

_REPO_ROOT = Path(__file__).resolve().parents[2]
_REQUIREMENTS = _REPO_ROOT / "requirements-semgrep.txt"
_PYPROJECT = _REPO_ROOT / "pyproject.toml"
_UV_LOCK = _REPO_ROOT / "uv.lock"
_MANIFEST = _REPO_ROOT / "lintro" / "tools" / "manifest.json"
_GENERATED = _REPO_ROOT / "lintro" / "_generated_versions.py"
_INSTALL_SCRIPT = _REPO_ROOT / "scripts" / "utils" / "install-semgrep.sh"
_INSTALL_TOOLS = _REPO_ROOT / "scripts" / "utils" / "install-tools.sh"
_COMPILE_SCRIPT = _REPO_ROOT / "scripts" / "ci" / "compile-semgrep-lock.sh"
_IN_FILE = _REPO_ROOT / "requirements-semgrep.in"
_TOOLS_PUBLISH = _REPO_ROOT / ".github" / "workflows" / "docker-tools-publish.yml"
_PIN_RE = re.compile(r"^semgrep==([0-9][^\s\\]+)", re.MULTILINE)
_IN_PIN_RE = re.compile(r"^semgrep==([0-9][^\s]+)\s*$", re.MULTILINE)


def _semgrep_pin() -> str:
    """Return the exact semgrep pin from the isolated requirements file.

    Returns:
        Pinned semgrep version string.
    """
    text = _REQUIREMENTS.read_text(encoding="utf-8")
    match = _PIN_RE.search(text)
    assert_that(match).is_not_none()
    assert match is not None
    return match.group(1)


def test_semgrep_requirements_file_exists_and_is_fully_pinned() -> None:
    """The isolated lockfile exists, pins every package, and includes hashes."""
    text = _REQUIREMENTS.read_text(encoding="utf-8")
    assert_that(text).is_not_empty()
    assert_that(text).contains("--hash=sha256:")
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip().rstrip("\\").strip()
        if not line or line.startswith("--"):
            continue
        assert_that(line).contains("==")


def test_semgrep_in_pin_matches_compiled_lockfile() -> None:
    """The committed .in pin and the compiled lockfile agree on semgrep."""
    in_text = _IN_FILE.read_text(encoding="utf-8")
    in_match = _IN_PIN_RE.search(in_text)
    assert_that(in_match).is_not_none()
    assert in_match is not None
    assert_that(in_match.group(1)).is_equal_to(_semgrep_pin())
    compile_script = _COMPILE_SCRIPT.read_text(encoding="utf-8")
    assert_that(compile_script).contains("--generate-hashes")
    assert_that(compile_script).contains("requirements-semgrep.in")
    assert_that(_COMPILE_SCRIPT.stat().st_mode & 0o111).is_not_equal_to(0)


def test_semgrep_requirements_pin_matches_manifest() -> None:
    """The requirements pin and the tool manifest agree on semgrep's version."""
    pin = _semgrep_pin()
    manifest = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    entry = next(tool for tool in manifest["tools"] if tool["name"] == "semgrep")
    assert_that(entry["version"]).is_equal_to(pin)
    generated = _GENERATED.read_text(encoding="utf-8")
    assert_that(generated).contains(f'"semgrep": "{pin}"')


def test_tools_extra_does_not_include_semgrep() -> None:
    """``lintro[tools]`` must not pull semgrep into the shared resolver."""
    pyproject = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    extras = pyproject["project"]["optional-dependencies"]
    tools = extras["tools"]
    assert_that(any(item.startswith("semgrep") for item in tools)).is_false()
    assert_that(any(item.startswith("sqlfluff") for item in tools)).is_true()
    assert_that(any(item.startswith("pip-audit") for item in tools)).is_true()
    uv = pyproject["tool"]["uv"]
    assert_that(uv).does_not_contain_key("override-dependencies")


def test_uv_lock_has_no_semgrep_package() -> None:
    """The shared lockfile must not retain a semgrep package entry."""
    text = _UV_LOCK.read_text(encoding="utf-8")
    assert_that(text).does_not_contain('name = "semgrep"')


def test_install_semgrep_script_uses_locked_sync() -> None:
    """The install script syncs the committed lockfile into a dedicated venv."""
    text = _INSTALL_SCRIPT.read_text(encoding="utf-8")
    assert_that(text).contains("uv venv")
    assert_that(text).contains("uv pip sync")
    assert_that(text).contains("uv pip check")
    assert_that(text).contains("requirements-semgrep.txt")
    executable_lines = [
        line
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert_that("\n".join(executable_lines)).does_not_contain("uv tool install")
    assert_that(_INSTALL_SCRIPT.stat().st_mode & 0o111).is_not_equal_to(0)
    tools_installer = _INSTALL_TOOLS.read_text(encoding="utf-8")
    assert_that(tools_installer).contains("install-semgrep.sh")
    assert_that(tools_installer).does_not_contain(
        'install_python_package "semgrep"',
    )


def test_tools_dockerfile_copies_semgrep_lockfile() -> None:
    """The tools image must copy the isolated lockfile into the build context."""
    dockerfile = _REPO_ROOT / "docker" / "tools.Dockerfile"
    text = dockerfile.read_text(encoding="utf-8")
    assert_that(text).contains("COPY requirements-semgrep.txt")
    assert_that(text).contains("/opt/semgrep-venv")


def test_ci_dockerfile_installs_isolated_semgrep() -> None:
    """The CI image must re-sync isolated semgrep from this build's lockfile.

    The root image is FROM a digest-pinned tools base, so a lockfile bump
    would otherwise dogfood the previous binary and skip on version lag
    until the tools digest is republished.
    """
    dockerfile = _REPO_ROOT / "Dockerfile"
    text = dockerfile.read_text(encoding="utf-8")
    assert_that(text).contains("COPY requirements-semgrep.txt")
    assert_that(text).contains("COPY scripts/utils/install-semgrep.sh")
    assert_that(text).contains("install-semgrep.sh --docker")
    assert_that(text).contains("/opt/semgrep-venv")
    assert_that(text).contains("semgrep --version")


def test_tools_publish_workflow_rebuilds_on_semgrep_isolation_changes() -> None:
    """Lockfile or installer edits must rebuild the tools image."""
    text = _TOOLS_PUBLISH.read_text(encoding="utf-8")
    assert_that(text).contains("scripts/utils/install-semgrep.sh")
    assert_that(text).contains("requirements-semgrep.txt")


def test_docker_ci_full_lint_includes_semgrep_lockfile() -> None:
    """A semgrep lockfile bump must force full-repo dogfood, not changed-files."""
    docker_ci = (_REPO_ROOT / ".github" / "workflows" / "docker-ci.yml").read_text(
        encoding="utf-8",
    )
    assert_that(docker_ci).contains("requirements-semgrep.in")
    assert_that(docker_ci).contains("requirements-semgrep.txt")


def test_install_semgrep_script_help_and_dry_run() -> None:
    """The installer documents usage and dry-run does not mutate the system."""
    help_result = subprocess.run(  # nosec B603 - fixed argv, shell=False
        [str(_INSTALL_SCRIPT), "--help"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert_that(help_result.returncode).is_equal_to(0)
    assert_that(help_result.stdout).contains("requirements-semgrep.txt")

    dry_run = subprocess.run(  # nosec B603 - fixed argv, shell=False
        [str(_INSTALL_SCRIPT), "--dry-run", "--local"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert_that(dry_run.returncode).is_equal_to(0)
    assert_that(dry_run.stdout).contains("[DRY-RUN]")
    assert_that(dry_run.stdout).contains("uv pip sync")

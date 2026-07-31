"""Tests for scripts/ci/release-bump-only.sh (#1362).

The script classifies automated release version-bump PRs with two layers:
identity signals (bot author, chore(release) title, release/v* branch) only
nominate; a diff allowlist plus version-stamp-only content checks decide.
These tests drive both layers against synthetic git repositories.
"""

import subprocess  # nosec B404 - subprocess drives the script under test; shell=False
from pathlib import Path

import pytest
from assertpy import assert_that

SCRIPT_PATH = Path("scripts/ci/release-bump-only.sh").resolve()

NOMINATED_ENV = {
    "EVENT_NAME": "pull_request",
    "PR_AUTHOR": "lgtm-release-bot[bot]",
    "PR_TITLE": "chore(release): version 0.2.0",
    "HEAD_REF": "release/v0.2.0",
}

PYPROJECT_TEMPLATE = """\
[build-system]
requires = ["setuptools"]

[project]
name = "lintro"
version = "{version}"
dependencies = [
  "click=={click_version}",
]

[tool.ruff]
line-length = 88
"""

UV_LOCK_TEMPLATE = """\
version = 1
requires-python = ">=3.11"

[[package]]
name = "click"
version = "{click_version}"
source = {{ registry = "https://pypi.org/simple" }}
sdist = {{ url = "https://example.invalid/click.tar.gz", hash = "sha256:{click_hash}" }}

[[package]]
name = "lintro"
version = "{version}"
source = {{ editable = "." }}
dependencies = [
    {{ name = "click" }},
]

[package.metadata]
requires-dist = [{{ name = "click", specifier = "=={click_version}" }}]
"""

INIT_TEMPLATE = '"""Lintro."""\n\n__version__ = "{version}"\n'

SECURITY_TEMPLATE = """\
# Security Policy

## Supported Versions

| Version | Supported |
| ------- | --------- |
| {line}.x  | ✅        |
| < {line}  | ❌        |

## Reporting

Report privately via a security advisory.
"""


def _write_security(
    repo: Path,
    line: str,
    path: str = "SECURITY.md",
    extra: str = "",
) -> None:
    """Write a SECURITY.md with the support table stamped to ``line``.

    Args:
        repo: Repository working directory.
        line: The ``major.minor`` support line (e.g. ``"0.1"``).
        path: Repo-relative SECURITY.md path.
        extra: Optional trailing prose appended outside the support table
            (used to simulate a non-conforming edit).
    """
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(SECURITY_TEMPLATE.format(line=line) + extra)


def _git(repo: Path, *args: str) -> str:
    """Run a git command inside the fixture repository.

    Args:
        repo: Repository working directory.
        *args: git subcommand and arguments.

    Returns:
        str: Captured stdout, stripped.
    """
    result = (
        subprocess.run(  # nosec B603 B607 - fixed git argv in a test-owned temp repo
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            check=True,
            env={
                "PATH": "/usr/bin:/bin:/usr/local/bin",
                "GIT_AUTHOR_NAME": "t",
                "GIT_AUTHOR_EMAIL": "t@example.invalid",
                "GIT_COMMITTER_NAME": "t",
                "GIT_COMMITTER_EMAIL": "t@example.invalid",
                "HOME": str(repo),
            },
        )
    )
    return result.stdout.strip()


def _write_release_files(
    repo: Path,
    version: str,
    click_version: str = "8.1.7",
    click_hash: str = "aa",
    changelog: str = "# Changelog\n",
) -> None:
    """Write the four release-touched files at the given versions.

    Args:
        repo: Repository working directory.
        version: Project version stamp to write.
        click_version: Pinned click version for pyproject/uv.lock.
        click_hash: Fake sdist hash for the click uv.lock entry.
        changelog: CHANGELOG.md content.
    """
    (repo / "pyproject.toml").write_text(
        PYPROJECT_TEMPLATE.format(version=version, click_version=click_version),
    )
    (repo / "uv.lock").write_text(
        UV_LOCK_TEMPLATE.format(
            version=version,
            click_version=click_version,
            click_hash=click_hash,
        ),
    )
    (repo / "lintro").mkdir(exist_ok=True)
    (repo / "lintro" / "__init__.py").write_text(
        INIT_TEMPLATE.format(version=version),
    )
    (repo / "CHANGELOG.md").write_text(changelog)


@pytest.fixture
def bump_repo(tmp_path: Path) -> Path:
    """Create a git repo with a base commit at version 0.1.0.

    Args:
        tmp_path: Pytest-provided temporary directory.

    Returns:
        Path: The repository working directory.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _write_release_files(repo, version="0.1.0")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    return repo


def _run_script(
    repo: Path,
    env: dict[str, str],
    output_file: Path,
) -> subprocess.CompletedProcess[str]:
    """Run release-bump-only.sh inside the fixture repository.

    Args:
        repo: Repository working directory.
        env: Script environment (identity signals, SHA overrides).
        output_file: File to expose as GITHUB_OUTPUT.

    Returns:
        subprocess.CompletedProcess[str]: The completed script run.
    """
    return subprocess.run(  # nosec B603 - fixed argv against the script under test; shell=False
        [str(SCRIPT_PATH)],
        capture_output=True,
        text=True,
        check=False,
        cwd=repo,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "GITHUB_OUTPUT": str(output_file),
            **env,
        },
    )


def _classify(
    repo: Path,
    tmp_path: Path,
    env_overrides: dict[str, str] | None = None,
) -> tuple[str, str]:
    """Commit the working tree as the head and classify base..head.

    Args:
        repo: Repository with a clean base commit and dirty working tree.
        tmp_path: Pytest-provided temporary directory.
        env_overrides: Extra/overriding script environment entries.

    Returns:
        tuple[str, str]: (release-bump output value, combined stdout+stderr).
    """
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "head")
    head = _git(repo, "rev-parse", "HEAD")
    output_file = tmp_path / "github-output"
    env = {**NOMINATED_ENV, "BASE_SHA": base, "HEAD_SHA": head}
    env.update(env_overrides or {})
    result = _run_script(repo, env, output_file)
    assert_that(result.returncode).is_equal_to(0)
    lines = output_file.read_text().splitlines()
    values = [line.removeprefix("release-bump=") for line in lines]
    assert_that(values).is_length(1)
    return values[0], result.stdout + result.stderr


def test_release_bump_only_help() -> None:
    """release-bump-only.sh should provide help and exit 0."""
    result = subprocess.run(  # nosec B603 - fixed argv against the script under test; shell=False
        [str(SCRIPT_PATH), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("Usage:")


def test_clean_version_bump_qualifies(bump_repo: Path, tmp_path: Path) -> None:
    """A version-stamp-only diff plus CHANGELOG entry resolves true.

    Args:
        bump_repo: Fixture repository at version 0.1.0.
        tmp_path: Pytest-provided temporary directory.
    """
    _write_release_files(
        bump_repo,
        version="0.2.0",
        changelog="# Changelog\n\n## 0.2.0\n",
    )
    verdict, _ = _classify(bump_repo, tmp_path)
    assert_that(verdict).is_equal_to("true")


@pytest.mark.parametrize(
    ("env_overrides", "expected_log"),
    [
        ({"PR_AUTHOR": "TurboCoder13"}, "not nominated: author"),
        ({"PR_TITLE": "feat: version 0.2.0"}, "not nominated: title"),
        ({"HEAD_REF": "feat/version-bump"}, "not nominated: head ref"),
        ({"EVENT_NAME": "push"}, "not a pull_request event"),
    ],
)
def test_identity_signal_mismatch_fails_closed(
    bump_repo: Path,
    tmp_path: Path,
    env_overrides: dict[str, str],
    expected_log: str,
) -> None:
    """Any missing identity signal resolves false even for a clean diff.

    Args:
        bump_repo: Fixture repository at version 0.1.0.
        tmp_path: Pytest-provided temporary directory.
        env_overrides: Identity signal to break.
        expected_log: Log fragment explaining the rejection.
    """
    _write_release_files(bump_repo, version="0.2.0")
    verdict, log = _classify(bump_repo, tmp_path, env_overrides)
    assert_that(verdict).is_equal_to("false")
    assert_that(log).contains(expected_log)


def test_file_outside_allowlist_fails(bump_repo: Path, tmp_path: Path) -> None:
    """A nominated PR touching a non-allowlisted file resolves false.

    Args:
        bump_repo: Fixture repository at version 0.1.0.
        tmp_path: Pytest-provided temporary directory.
    """
    _write_release_files(bump_repo, version="0.2.0")
    (bump_repo / "lintro" / "cli.py").write_text("print('payload')\n")
    verdict, log = _classify(bump_repo, tmp_path)
    assert_that(verdict).is_equal_to("false")
    assert_that(log).contains("outside allowlist")


def test_pyproject_dependency_change_fails(
    bump_repo: Path,
    tmp_path: Path,
) -> None:
    """A dependency pin change in pyproject.toml resolves false.

    Args:
        bump_repo: Fixture repository at version 0.1.0.
        tmp_path: Pytest-provided temporary directory.
    """
    _write_release_files(bump_repo, version="0.2.0", click_version="8.2.0")
    verdict, log = _classify(bump_repo, tmp_path)
    assert_that(verdict).is_equal_to("false")
    assert_that(log).contains("pyproject.toml changed beyond")


def test_uv_lock_external_package_change_fails(
    bump_repo: Path,
    tmp_path: Path,
) -> None:
    """An external-package uv.lock change resolves false (spoof guard).

    The diff touches only allowlisted files and only `version = "..."` /
    hash lines, so a bare line-level check would pass — the block-aware
    strip must reject it.

    Args:
        bump_repo: Fixture repository at version 0.1.0.
        tmp_path: Pytest-provided temporary directory.
    """
    _write_release_files(bump_repo, version="0.2.0")
    lock = bump_repo / "uv.lock"
    # Only uv.lock carries the dependency change; pyproject keeps its pin.
    lock.write_text(
        lock.read_text().replace('version = "8.1.7"', 'version = "8.2.0"'),
    )
    verdict, log = _classify(bump_repo, tmp_path)
    assert_that(verdict).is_equal_to("false")
    assert_that(log).contains("uv.lock changed beyond")


def test_init_change_beyond_version_fails(
    bump_repo: Path,
    tmp_path: Path,
) -> None:
    """Extra code in lintro/__init__.py resolves false.

    Args:
        bump_repo: Fixture repository at version 0.1.0.
        tmp_path: Pytest-provided temporary directory.
    """
    _write_release_files(bump_repo, version="0.2.0")
    init_file = bump_repo / "lintro" / "__init__.py"
    init_file.write_text(init_file.read_text() + "import os  # payload\n")
    verdict, log = _classify(bump_repo, tmp_path)
    assert_that(verdict).is_equal_to("false")
    assert_that(log).contains("__init__.py changed beyond __version__")


def test_security_table_bump_qualifies(bump_repo: Path, tmp_path: Path) -> None:
    """A minor bump that only restamps the SECURITY.md tables resolves true.

    The version PR now rewrites the supported-versions rows on minor/major
    bumps (#1372); such a diff must stay bump-only (#1362).

    Args:
        bump_repo: Fixture repository at version 0.1.0.
        tmp_path: Pytest-provided temporary directory.
    """
    # Seed both SECURITY.md files at the base line and fold them into the base.
    _write_security(bump_repo, line="0.1")
    _write_security(bump_repo, line="0.1", path=".github/SECURITY.md")
    _git(bump_repo, "add", "-A")
    _git(bump_repo, "commit", "-q", "-m", "seed security policy")

    # Minor bump: version stamp + CHANGELOG + support-table rows only.
    _write_release_files(
        bump_repo,
        version="0.2.0",
        changelog="# Changelog\n\n## 0.2.0\n",
    )
    _write_security(bump_repo, line="0.2")
    _write_security(bump_repo, line="0.2", path=".github/SECURITY.md")

    verdict, log = _classify(bump_repo, tmp_path)
    assert_that(verdict).is_equal_to("true")
    assert_that(log).contains("SECURITY table")


def test_security_edit_beyond_table_fails(
    bump_repo: Path,
    tmp_path: Path,
) -> None:
    """A SECURITY.md edit outside the support table rows resolves false.

    Args:
        bump_repo: Fixture repository at version 0.1.0.
        tmp_path: Pytest-provided temporary directory.
    """
    _write_security(bump_repo, line="0.1")
    _git(bump_repo, "add", "-A")
    _git(bump_repo, "commit", "-q", "-m", "seed security policy")

    _write_release_files(bump_repo, version="0.2.0")
    # Restamp the table *and* sneak in unrelated prose beyond the rows.
    _write_security(bump_repo, line="0.2", extra="\nInjected policy change.\n")

    verdict, log = _classify(bump_repo, tmp_path)
    assert_that(verdict).is_equal_to("false")
    assert_that(log).contains("SECURITY.md changed beyond the support table")


def test_github_security_edit_beyond_table_fails(
    bump_repo: Path,
    tmp_path: Path,
) -> None:
    """A .github/SECURITY.md edit outside the support table resolves false.

    Args:
        bump_repo: Fixture repository at version 0.1.0.
        tmp_path: Pytest-provided temporary directory.
    """
    _write_security(bump_repo, line="0.1", path=".github/SECURITY.md")
    _git(bump_repo, "add", "-A")
    _git(bump_repo, "commit", "-q", "-m", "seed github security policy")

    _write_release_files(bump_repo, version="0.2.0")
    _write_security(
        bump_repo,
        line="0.2",
        path=".github/SECURITY.md",
        extra="\nUnauthorized addition.\n",
    )

    verdict, log = _classify(bump_repo, tmp_path)
    assert_that(verdict).is_equal_to("false")
    assert_that(log).contains(".github/SECURITY.md changed beyond the support table")


def test_empty_diff_fails(bump_repo: Path, tmp_path: Path) -> None:
    """An empty diff resolves false.

    Args:
        bump_repo: Fixture repository at version 0.1.0.
        tmp_path: Pytest-provided temporary directory.
    """
    base = _git(bump_repo, "rev-parse", "HEAD")
    output_file = tmp_path / "github-output"
    env = {**NOMINATED_ENV, "BASE_SHA": base, "HEAD_SHA": base}
    result = _run_script(bump_repo, env, output_file)
    assert_that(result.returncode).is_equal_to(0)
    assert_that(output_file.read_text()).contains("release-bump=false")
    assert_that(result.stdout).contains("empty diff")


def test_non_merge_head_without_shas_fails_closed(
    bump_repo: Path,
    tmp_path: Path,
) -> None:
    """Without SHA overrides, a non-merge HEAD resolves false.

    Args:
        bump_repo: Fixture repository at version 0.1.0.
        tmp_path: Pytest-provided temporary directory.
    """
    output_file = tmp_path / "github-output"
    result = _run_script(bump_repo, dict(NOMINATED_ENV), output_file)
    assert_that(result.returncode).is_equal_to(0)
    assert_that(output_file.read_text()).contains("release-bump=false")
    assert_that(result.stderr).contains("not a PR merge commit")


def test_merge_commit_diff_range_qualifies(
    bump_repo: Path,
    tmp_path: Path,
) -> None:
    """A PR-style merge commit derives its own base..head range.

    Args:
        bump_repo: Fixture repository at version 0.1.0.
        tmp_path: Pytest-provided temporary directory.
    """
    _git(bump_repo, "checkout", "-q", "-b", "release/v0.2.0")
    _write_release_files(bump_repo, version="0.2.0")
    _git(bump_repo, "add", "-A")
    _git(bump_repo, "commit", "-q", "-m", "chore(release): version 0.2.0")
    _git(bump_repo, "checkout", "-q", "-")
    _git(
        bump_repo,
        "merge",
        "-q",
        "--no-ff",
        "-m",
        "merge",
        "release/v0.2.0",
    )
    output_file = tmp_path / "github-output"
    result = _run_script(bump_repo, dict(NOMINATED_ENV), output_file)
    assert_that(result.returncode).is_equal_to(0)
    assert_that(output_file.read_text()).contains("release-bump=true")

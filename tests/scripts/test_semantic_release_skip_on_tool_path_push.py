"""Tests for `semantic-release-helpers.sh skip_on_tool_path_push`.

The push trigger of `semantic-release.yml` must hand off to `workflow_run`
whenever a commit touches a tools-image input — running here would race
the tools-image build and pin a stale digest. These tests seed a tmp git
repo with controllable BEFORE/AFTER commits and assert the helper emits
`skip=true|false` to the fake `GITHUB_OUTPUT` file accordingly.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from assertpy import assert_that

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "ci" / "github" / "semantic-release-helpers.sh"

ZERO_SHA = "0" * 40


def _git(cwd: Path, *args: str) -> str:
    """Run git in ``cwd`` and return stripped stdout.

    Args:
        cwd: Working directory for the git invocation.
        *args: Arguments forwarded to git.

    Returns:
        Trimmed stdout of the command.
    """
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _init_repo(tmp_path: Path) -> Path:
    """Create a tmp git repo with an initial non-tool commit.

    Args:
        tmp_path: Base tmp directory from the pytest fixture.

    Returns:
        Path to the created repo.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("initial\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "chore: init")
    return repo


def _run_helper(
    repo: Path,
    *,
    before: str,
    after: str,
    output_file: Path,
) -> subprocess.CompletedProcess[str]:
    """Invoke the helper with the given env and capture its output.

    Args:
        repo: Working directory (a git repo).
        before: Value for ``BEFORE_SHA``.
        after: Value for ``AFTER_SHA``.
        output_file: Path to write ``$GITHUB_OUTPUT`` entries to.

    Returns:
        Completed subprocess with captured streams.
    """
    return subprocess.run(
        ["bash", str(SCRIPT), "skip_on_tool_path_push"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        env={
            "PATH": "/usr/bin:/bin",
            "BEFORE_SHA": before,
            "AFTER_SHA": after,
            "GITHUB_OUTPUT": str(output_file),
        },
    )


def _commit_file(repo: Path, path: str, content: str = "x\n") -> str:
    """Add/modify a tracked file and commit. Returns the new HEAD sha."""
    full = repo / path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)
    _git(repo, "add", path)
    _git(repo, "commit", "-q", "-m", f"touch {path}")
    return _git(repo, "rev-parse", "HEAD")


def test_skip_true_when_tools_image_workflow_changed(tmp_path: Path) -> None:
    """Editing .github/workflows/tools-image.yml must defer the release."""
    repo = _init_repo(tmp_path)
    before = _git(repo, "rev-parse", "HEAD")
    after = _commit_file(repo, ".github/workflows/tools-image.yml", "name: x\n")
    out = tmp_path / "out"
    out.touch()

    result = _run_helper(repo, before=before, after=after, output_file=out)

    assert_that(result.returncode).described_as(result.stderr).is_equal_to(0)
    assert_that(out.read_text()).contains("skip=true")
    assert_that(result.stdout).contains("deferring to workflow_run")


def test_skip_true_when_tools_image_script_changed(tmp_path: Path) -> None:
    """Glob branch: any `scripts/ci/tools-image-*.sh` edit triggers skip."""
    repo = _init_repo(tmp_path)
    before = _git(repo, "rev-parse", "HEAD")
    after = _commit_file(
        repo,
        "scripts/ci/tools-image-resolve-digest.sh",
        "#!/bin/sh\n",
    )
    out = tmp_path / "out"
    out.touch()

    result = _run_helper(repo, before=before, after=after, output_file=out)

    assert_that(result.returncode).is_equal_to(0)
    assert_that(out.read_text()).contains("skip=true")


def test_skip_false_for_unrelated_change(tmp_path: Path) -> None:
    """A docs-only commit must not defer the release."""
    repo = _init_repo(tmp_path)
    before = _git(repo, "rev-parse", "HEAD")
    after = _commit_file(repo, "docs/guide.md", "# guide\n")
    out = tmp_path / "out"
    out.touch()

    result = _run_helper(repo, before=before, after=after, output_file=out)

    assert_that(result.returncode).is_equal_to(0)
    assert_that(out.read_text()).contains("skip=false")


def test_zero_sha_falls_back_to_git_show(tmp_path: Path) -> None:
    """First push on a new branch has BEFORE_SHA==0..0; helper must still decide.

    The `git diff $before $after` path requires two real refs, so the helper
    switches to `git show` when BEFORE_SHA is the zero-sha sentinel. This
    test exercises that branch by passing all zeros alongside a tool-path
    change.
    """
    repo = _init_repo(tmp_path)
    after = _commit_file(repo, "Dockerfile.tools", "FROM scratch\n")
    out = tmp_path / "out"
    out.touch()

    result = _run_helper(repo, before=ZERO_SHA, after=after, output_file=out)

    assert_that(result.returncode).described_as(result.stderr).is_equal_to(0)
    assert_that(out.read_text()).contains("skip=true")


def test_multiple_files_any_tool_path_forces_skip(tmp_path: Path) -> None:
    """A mixed commit with one tool-path file must still defer."""
    repo = _init_repo(tmp_path)
    before = _git(repo, "rev-parse", "HEAD")
    (repo / "docs").mkdir()
    (repo / "docs" / "guide.md").write_text("# guide\n")
    (repo / "package.json").write_text("{}\n")
    _git(repo, "add", "docs/guide.md", "package.json")
    _git(repo, "commit", "-q", "-m", "mixed")
    after = _git(repo, "rev-parse", "HEAD")
    out = tmp_path / "out"
    out.touch()

    result = _run_helper(repo, before=before, after=after, output_file=out)

    assert_that(result.returncode).is_equal_to(0)
    assert_that(out.read_text()).contains("skip=true")

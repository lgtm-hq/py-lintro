"""Tests for shell scripts in the scripts/ directory.

This module tests the shell scripts to ensure they follow best practices,
have correct syntax, and provide appropriate help/usage information.
"""

import subprocess  # nosec B404 - subprocess is used to drive the tool/CLI under test; invocations use shell=False
from pathlib import Path

import pytest
from assertpy import assert_that

RESOLVE_SCRIPT_PATH = Path("scripts/ci/resolve-pipeline-relevance.sh").resolve()


def _git(repo: Path, *args: str) -> str:
    """Run a git command inside a fixture repository.

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


@pytest.fixture
def diff_repo(tmp_path: Path) -> Path:
    """Create a git repo with a seed base commit for diff classification.

    Args:
        tmp_path: Pytest-provided temporary directory.

    Returns:
        Path: The repository working directory.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "seed.txt").write_text("seed\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    return repo


def _commit_paths(repo: Path, paths: list[str]) -> tuple[str, str]:
    """Write and commit the given paths, returning the (base, head) SHAs.

    Args:
        repo: Repository with a clean base commit.
        paths: Repository-relative file paths to create/modify.

    Returns:
        tuple[str, str]: (base SHA before the commit, head SHA after).
    """
    base = _git(repo, "rev-parse", "HEAD")
    for path in paths:
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"content of {path}\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "head")
    head = _git(repo, "rev-parse", "HEAD")
    return base, head


def _run_resolve_pipeline_relevance(
    env: dict[str, str],
    output_file: Path,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run resolve-pipeline-relevance.sh with a GITHUB_OUTPUT file.

    Args:
        env: EVENT_NAME/CHANGES_JSON/SHA environment for the script.
        output_file: File to expose as GITHUB_OUTPUT.
        cwd: Working directory for the run (a fixture git repo when the
            deny-by-default diff classification is under test).

    Returns:
        subprocess.CompletedProcess[str]: The completed script run.
    """
    return subprocess.run(  # nosec B603 - fixed argv run against a real binary in a controlled test; shell=False, no user shell input
        [str(RESOLVE_SCRIPT_PATH)],
        capture_output=True,
        text=True,
        check=False,
        cwd=cwd,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "GITHUB_OUTPUT": str(output_file),
            **env,
        },
    )


def _classify_paths(
    diff_repo: Path,
    tmp_path: Path,
    paths: list[str],
    env_overrides: dict[str, str] | None = None,
) -> tuple[list[str], str]:
    """Classify a synthetic PR diff touching only the given paths.

    Args:
        diff_repo: Fixture repository with a seed base commit.
        tmp_path: Pytest-provided temporary directory.
        paths: Changed paths making up the PR diff.
        env_overrides: Extra/overriding script environment entries.

    Returns:
        tuple[list[str], str]: (GITHUB_OUTPUT lines, stdout+stderr).
    """
    base, head = _commit_paths(diff_repo, paths)
    output_file = tmp_path / "github-output"
    env = {"EVENT_NAME": "pull_request", "BASE_SHA": base, "HEAD_SHA": head}
    env.update(env_overrides or {})
    result = _run_resolve_pipeline_relevance(env, output_file, cwd=diff_repo)
    assert_that(result.returncode).is_equal_to(0)
    return output_file.read_text().splitlines(), result.stdout + result.stderr


def test_detect_changes_help() -> None:
    """detect-changes.sh should provide help and exit 0."""
    script_path = Path("scripts/ci/detect-changes.sh").resolve()
    result = subprocess.run(  # nosec B603 - fixed argv run against a real binary in a controlled test; shell=False, no user shell input
        [str(script_path), "--help"],
        capture_output=True,
        text=True,
    )
    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("Usage:")


def test_resolve_pipeline_relevance_help() -> None:
    """resolve-pipeline-relevance.sh should provide help and exit 0."""
    result = subprocess.run(  # nosec B603 - fixed argv run against a real binary in a controlled test; shell=False, no user shell input
        [str(RESOLVE_SCRIPT_PATH), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("Usage:")


def test_resolve_pipeline_relevance_non_pr_events_never_skip(
    tmp_path: Path,
) -> None:
    """merge_group, push, and dispatch always resolve pipeline=true.

    Args:
        tmp_path: Pytest-provided temporary directory.
    """
    for index, event in enumerate(("merge_group", "push", "workflow_dispatch")):
        output_file = tmp_path / f"github-output-{index}"
        result = _run_resolve_pipeline_relevance(
            {"EVENT_NAME": event},
            output_file,
            cwd=tmp_path,
        )
        assert_that(result.returncode).is_equal_to(0)
        lines = output_file.read_text().splitlines()
        assert_that(lines).contains("pipeline=true")
        assert_that(lines).contains("lint-scope=full")


@pytest.mark.parametrize(
    ("paths", "expected"),
    [
        # Skip-list: pure prose and assets resolve pipeline=false.
        (["README.md"], "pipeline=false"),
        (["docs/guide.md"], "pipeline=false"),
        (["docs/tutorial/example.py"], "pipeline=false"),
        (["assets/logo.svg"], "pipeline=false"),
        (["README.md", "docs/guide.md", "assets/logo.svg"], "pipeline=false"),
        # Markdown at any depth is prose, wherever it lives.
        (["lintro/README.md"], "pipeline=false"),
        # Any file outside the skip-list runs the pipeline.
        (["lintro/core.py"], "pipeline=true"),
        # A single relevant file poisons an otherwise skippable diff.
        (["README.md", "docs/guide.md", "lintro/core.py"], "pipeline=true"),
        # Deny-by-default (#1369): brand-new top-level directories and root
        # files trigger without anyone extending an allow-list.
        (["new_build_system/build.gradle"], "pipeline=true"),
        (["justfile"], "pipeline=true"),
        (["infra/terraform/main.tf"], "pipeline=true"),
        # Carve-outs: lint fixtures and lint config that match the skip
        # globs but feed the integration tests / lint behavior.
        (["test_samples/sample.md"], "pipeline=true"),
        (["test_samples/violations.py"], "pipeline=true"),
        (["docs/.markdownlint-cli2.jsonc"], "pipeline=true"),
    ],
)
def test_resolve_pipeline_relevance_deny_by_default(
    diff_repo: Path,
    tmp_path: Path,
    paths: list[str],
    expected: str,
) -> None:
    """pipeline=false only when EVERY changed file matches the skip-list.

    Args:
        diff_repo: Fixture repository with a seed base commit.
        tmp_path: Pytest-provided temporary directory.
        paths: Changed paths making up the PR diff.
        expected: Expected pipeline output line.
    """
    lines, _ = _classify_paths(diff_repo, tmp_path, paths)
    assert_that(lines).contains(expected)
    if expected == "pipeline=false":
        assert_that(lines).contains("skip-reason=docs-only change")
    else:
        assert_that(lines).contains("skip-reason=")


def test_resolve_pipeline_relevance_rename_into_skippable_path_triggers(
    diff_repo: Path,
    tmp_path: Path,
) -> None:
    """Renaming a relevant file into the skip-list keeps the pipeline on.

    git rename detection would report only the destination path
    (docs/core.py), classifying the move as docs-only; the script must diff
    with --no-renames so the deleted source path (lintro/core.py) is listed
    and keeps the pipeline running.

    Args:
        diff_repo: Fixture repository with a seed base commit.
        tmp_path: Pytest-provided temporary directory.
    """
    source = diff_repo / "lintro" / "core.py"
    source.parent.mkdir(parents=True)
    source.write_text("".join(f"line {i}\n" for i in range(50)))
    _git(diff_repo, "add", "-A")
    _git(diff_repo, "commit", "-q", "-m", "add module")
    base = _git(diff_repo, "rev-parse", "HEAD")
    (diff_repo / "docs").mkdir()
    _git(diff_repo, "mv", "lintro/core.py", "docs/core.py")
    _git(diff_repo, "commit", "-q", "-m", "move module under docs")
    head = _git(diff_repo, "rev-parse", "HEAD")
    output_file = tmp_path / "github-output"
    result = _run_resolve_pipeline_relevance(
        {"EVENT_NAME": "pull_request", "BASE_SHA": base, "HEAD_SHA": head},
        output_file,
        cwd=diff_repo,
    )
    assert_that(result.returncode).is_equal_to(0)
    assert_that(output_file.read_text().splitlines()).contains("pipeline=true")


def test_resolve_pipeline_relevance_fails_open_without_diff(
    tmp_path: Path,
) -> None:
    """An unavailable changed-file list fails open to pipeline=true.

    Covers a non-repo working directory and a repo whose HEAD is not a PR
    merge commit (no BASE_SHA/HEAD_SHA overrides in either case).

    Args:
        tmp_path: Pytest-provided temporary directory.
    """
    non_repo = tmp_path / "non-repo"
    non_repo.mkdir()
    plain_repo = tmp_path / "plain-repo"
    plain_repo.mkdir()
    _git(plain_repo, "init", "-q")
    (plain_repo / "file.txt").write_text("x\n")
    _git(plain_repo, "add", "-A")
    _git(plain_repo, "commit", "-q", "-m", "single")
    for index, cwd in enumerate((non_repo, plain_repo)):
        output_file = tmp_path / f"github-output-{index}"
        result = _run_resolve_pipeline_relevance(
            {"EVENT_NAME": "pull_request"},
            output_file,
            cwd=cwd,
        )
        assert_that(result.returncode).is_equal_to(0)
        assert_that(result.stdout).contains("failing open")
        lines = output_file.read_text().splitlines()
        assert_that(lines).contains("pipeline=true")
        assert_that(lines).contains("skip-reason=")


def test_resolve_pipeline_relevance_fails_open_on_empty_diff(
    diff_repo: Path,
    tmp_path: Path,
) -> None:
    """An empty diff (BASE_SHA == HEAD_SHA) fails open to pipeline=true.

    Args:
        diff_repo: Fixture repository with a seed base commit.
        tmp_path: Pytest-provided temporary directory.
    """
    head = _git(diff_repo, "rev-parse", "HEAD")
    output_file = tmp_path / "github-output"
    result = _run_resolve_pipeline_relevance(
        {"EVENT_NAME": "pull_request", "BASE_SHA": head, "HEAD_SHA": head},
        output_file,
        cwd=diff_repo,
    )
    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("failing open")
    assert_that(output_file.read_text().splitlines()).contains("pipeline=true")


def test_resolve_pipeline_relevance_derives_range_from_merge_commit(
    diff_repo: Path,
    tmp_path: Path,
) -> None:
    """Without SHA overrides, a PR-style merge commit supplies the range.

    Args:
        diff_repo: Fixture repository with a seed base commit.
        tmp_path: Pytest-provided temporary directory.
    """
    default_branch = _git(diff_repo, "rev-parse", "--abbrev-ref", "HEAD")
    _git(diff_repo, "checkout", "-q", "-b", "docs-change")
    (diff_repo / "README.md").write_text("# docs only\n")
    _git(diff_repo, "add", "-A")
    _git(diff_repo, "commit", "-q", "-m", "docs")
    _git(diff_repo, "checkout", "-q", default_branch)
    _git(diff_repo, "merge", "-q", "--no-ff", "-m", "merge", "docs-change")
    output_file = tmp_path / "github-output"
    result = _run_resolve_pipeline_relevance(
        {"EVENT_NAME": "pull_request"},
        output_file,
        cwd=diff_repo,
    )
    assert_that(result.returncode).is_equal_to(0)
    lines = output_file.read_text().splitlines()
    assert_that(lines).contains("pipeline=false")
    assert_that(lines).contains("skip-reason=docs-only change")


def test_resolve_pipeline_relevance_lint_scope(tmp_path: Path) -> None:
    """resolve-pipeline-relevance.sh should resolve lint-scope per event/JSON.

    lint-scope narrows to `changed` only when a pull_request diff explicitly
    missed the `full-lint` filter; every other case (non-PR events, filter
    hit, missing filter, unparsable JSON) stays full-repo. The PR cases run
    outside a git repo so the pipeline half fails open independently.

    Args:
        tmp_path: Pytest-provided temporary directory.
    """
    cases: list[tuple[dict[str, str], str]] = [
        # Non-PR events always lint the full repo.
        ({"EVENT_NAME": "merge_group"}, "lint-scope=full"),
        ({"EVENT_NAME": "push"}, "lint-scope=full"),
        ({"EVENT_NAME": "workflow_dispatch"}, "lint-scope=full"),
        # PRs narrow to changed files only on an explicit full-lint=false.
        (
            {
                "EVENT_NAME": "pull_request",
                "CHANGES_JSON": '{"full-lint":false}',
            },
            "lint-scope=changed",
        ),
        (
            {
                "EVENT_NAME": "pull_request",
                "CHANGES_JSON": '{"full-lint":true}',
            },
            "lint-scope=full",
        ),
        # Missing filter, empty, or unparsable JSON fail safe to full.
        ({"EVENT_NAME": "pull_request", "CHANGES_JSON": ""}, "lint-scope=full"),
        (
            {"EVENT_NAME": "pull_request", "CHANGES_JSON": "not json"},
            "lint-scope=full",
        ),
        ({"EVENT_NAME": "pull_request", "CHANGES_JSON": "{}"}, "lint-scope=full"),
    ]
    for index, (env, expected) in enumerate(cases):
        output_file = tmp_path / f"github-output-{index}"
        result = _run_resolve_pipeline_relevance(env, output_file, cwd=tmp_path)
        assert_that(result.returncode).is_equal_to(0)
        assert_that(output_file.read_text().splitlines()).contains(expected)


def test_resolve_pipeline_relevance_full_lint_invariant_guard(
    diff_repo: Path,
    tmp_path: Path,
) -> None:
    """A full-lint hit forces pipeline=true even on an all-skippable diff.

    A global-lint-impact file can hide inside the skip-list (e.g. a new
    dotfile config under docs/); the runtime guard must run the full
    pipeline rather than skipping the dogfooding lint of such a change.

    Args:
        diff_repo: Fixture repository with a seed base commit.
        tmp_path: Pytest-provided temporary directory.
    """
    lines, log = _classify_paths(
        diff_repo,
        tmp_path,
        ["docs/.prettierrc.json", "docs/guide.md"],
        env_overrides={"CHANGES_JSON": '{"full-lint":true}'},
    )
    assert_that(lines).contains("pipeline=true")
    assert_that(lines).contains("skip-reason=")
    assert_that(log).contains("forcing pipeline=true")


def test_resolve_pipeline_relevance_release_bump(
    diff_repo: Path,
    tmp_path: Path,
) -> None:
    """RELEASE_BUMP=true skips the pipeline on pull_request events (#1362).

    The override must outrank the deny-by-default classification and the
    full-lint drift guard (bump PRs always resolve pipeline=true via
    pyproject.toml/uv.lock), apply only to pull_request events, and only on
    the exact string "true".

    Args:
        diff_repo: Fixture repository with a seed base commit.
        tmp_path: Pytest-provided temporary directory.
    """
    bump_paths = ["CHANGELOG.md", "pyproject.toml", "uv.lock"]
    # Verified bump PR: skip even though the diff misses the skip-list.
    lines, _ = _classify_paths(
        diff_repo,
        tmp_path,
        bump_paths,
        env_overrides={
            "CHANGES_JSON": '{"full-lint":true}',
            "RELEASE_BUMP": "true",
        },
    )
    assert_that(lines).contains("pipeline=false")
    assert_that(lines).contains("skip-reason=version-bump PR")
    # Anything but the exact string "true" keeps the resolved value.
    for index, verdict in enumerate(("false", "garbage", "")):
        base = _git(diff_repo, "rev-parse", "HEAD^1")
        head = _git(diff_repo, "rev-parse", "HEAD")
        output_file = tmp_path / f"github-output-bump-{index}"
        result = _run_resolve_pipeline_relevance(
            {
                "EVENT_NAME": "pull_request",
                "BASE_SHA": base,
                "HEAD_SHA": head,
                "RELEASE_BUMP": verdict,
            },
            output_file,
            cwd=diff_repo,
        )
        assert_that(result.returncode).is_equal_to(0)
        lines = output_file.read_text().splitlines()
        assert_that(lines).contains("pipeline=true")
        assert_that(lines).contains("skip-reason=")
    # Non-PR events never skip, whatever RELEASE_BUMP claims.
    for index, event in enumerate(("push", "merge_group")):
        output_file = tmp_path / f"github-output-event-{index}"
        result = _run_resolve_pipeline_relevance(
            {"EVENT_NAME": event, "RELEASE_BUMP": "true"},
            output_file,
            cwd=tmp_path,
        )
        assert_that(result.returncode).is_equal_to(0)
        lines = output_file.read_text().splitlines()
        assert_that(lines).contains("pipeline=true")
        assert_that(lines).contains("skip-reason=")


def test_renovate_regex_manager_current_value() -> None:
    """Ensure Renovate custom managers use currentValue to satisfy schema."""
    config_path = Path("renovate.json")
    content = config_path.read_text()
    assert_that(content).contains("customManagers")
    assert_that(content).contains("currentValue")

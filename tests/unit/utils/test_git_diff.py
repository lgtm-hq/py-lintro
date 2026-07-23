"""Unit tests for git-diff based file selection (``--diff`` scanning)."""

from __future__ import annotations

import subprocess  # nosec B404 - subprocess is used to drive git under test; invocations use shell=False
from collections.abc import Iterator
from pathlib import Path

import pytest
from assertpy import assert_that

import lintro.utils.git_diff as git_diff
from lintro.utils.git_diff import (
    DIFF_DEFAULT_SENTINEL,
    DiffResolutionError,
    all_repo_defaults_resolvable,
    filter_files_by_diff,
    filter_files_by_diff_for_paths,
    get_changed_files,
    get_changed_files_for_paths,
    is_git_repository,
    resolve_default_base,
    resolve_git_cwd_from_paths,
)


@pytest.fixture(autouse=True)
def _clear_diff_cache() -> Iterator[None]:
    """Clear the per-run changed-file cache around each test.

    Yields:
        None: Control to the test body with a clean cache before and after.
    """
    get_changed_files.cache_clear()
    yield
    get_changed_files.cache_clear()


def _git(repo: Path, *args: str) -> None:
    """Run a git command inside ``repo``.

    Args:
        repo: Repository working directory.
        *args: Git arguments (without the leading ``git``).
    """
    subprocess.run(  # nosec B603 B607 - fixed argv run against git in a controlled test; binary name resolved from PATH, not attacker-controlled; shell=False, no user shell input
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Create a real git repository with a committed baseline on ``main``.

    Args:
        tmp_path: Pytest temporary directory.

    Returns:
        Path to the initialized repository with ``a.py`` and ``b.py`` on
        ``main`` and an active feature branch checked out.
    """
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test User")
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "b.py").write_text("y = 2\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "init")
    _git(tmp_path, "branch", "-M", "main")
    _git(tmp_path, "checkout", "-qb", "feature")
    return tmp_path


def _names(paths: frozenset[str] | list[str]) -> list[str]:
    """Return sorted basenames for a collection of file paths.

    Args:
        paths: File paths to reduce to basenames.

    Returns:
        Sorted list of basenames.
    """
    return sorted(Path(p).name for p in paths)


def test_is_git_repository_true(git_repo: Path) -> None:
    """A real repo is detected as a git working tree."""
    assert_that(is_git_repository(str(git_repo))).is_true()


def test_is_git_repository_false(tmp_path: Path) -> None:
    """A plain directory is not detected as a git working tree."""
    assert_that(is_git_repository(str(tmp_path))).is_false()


def test_resolve_default_base_prefers_main(git_repo: Path) -> None:
    """Default base resolves to an existing local ref (``main``)."""
    assert_that(resolve_default_base(str(git_repo))).is_equal_to("main")


def test_get_changed_files_committed_change(git_repo: Path) -> None:
    """Committed branch changes are reported relative to the base."""
    (git_repo / "a.py").write_text("x = 42\n")
    _git(git_repo, "commit", "-aqm", "change a")

    changed = get_changed_files("main", str(git_repo))

    assert_that(_names(changed)).is_equal_to(["a.py"])


def test_get_changed_files_includes_working_tree_and_untracked(
    git_repo: Path,
) -> None:
    """Unstaged edits and untracked files are part of the changed set."""
    (git_repo / "a.py").write_text("x = 7\n")  # unstaged modification
    (git_repo / "c.py").write_text("z = 3\n")  # untracked

    changed = get_changed_files("main", str(git_repo))

    assert_that(_names(changed)).is_equal_to(["a.py", "c.py"])


def test_get_changed_files_fallback_root_preserves_cwd_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fallback absolute root keeps ``abspath`` symlink semantics.

    Args:
        tmp_path: Temporary directory fixture.
        monkeypatch: Pytest monkeypatch fixture.
    """
    real_repo = tmp_path / "real"
    real_repo.mkdir()
    symlink_repo = tmp_path / "link"
    symlink_repo.symlink_to(real_repo, target_is_directory=True)
    (symlink_repo / "changed.py").write_text("x = 1\n")

    monkeypatch.setattr(git_diff, "_repo_root", lambda cwd: None)
    monkeypatch.setattr(git_diff, "ref_exists", lambda base, cwd: True)
    monkeypatch.setattr(
        git_diff,
        "_merge_base_diff_names",
        lambda base, cwd: ["changed.py"],
    )
    monkeypatch.setattr(git_diff, "_worktree_diff_names", lambda cwd, *, cached: [])
    monkeypatch.setattr(git_diff, "_untracked_names", lambda cwd: [])

    changed = get_changed_files("main", str(symlink_repo))

    assert_that(changed).contains(str(symlink_repo / "changed.py"))
    assert_that(changed).does_not_contain(str(real_repo / "changed.py"))


def test_get_changed_files_staged_change(git_repo: Path) -> None:
    """Staged (cached) changes are included."""
    (git_repo / "a.py").write_text("x = 5\n")
    _git(git_repo, "add", "a.py")

    changed = get_changed_files("main", str(git_repo))

    assert_that(_names(changed)).is_equal_to(["a.py"])


def test_get_changed_files_rename_has_no_phantom(git_repo: Path) -> None:
    """A rename yields the new path only; the old path is not a phantom."""
    _git(git_repo, "mv", "b.py", "renamed.py")
    _git(git_repo, "add", "renamed.py")

    changed = get_changed_files("main", str(git_repo))

    assert_that(_names(changed)).contains("renamed.py")
    assert_that(_names(changed)).does_not_contain("b.py")
    # No path in the set may be missing on disk.
    for path in changed:
        assert_that(Path(path).is_file()).is_true()


def test_get_changed_files_deletion_excluded(git_repo: Path) -> None:
    """A deleted file never appears in the changed set."""
    (git_repo / "b.py").unlink()
    _git(git_repo, "add", "-A")

    changed = get_changed_files("main", str(git_repo))

    assert_that(_names(changed)).does_not_contain("b.py")


def test_get_changed_files_unresolvable_base_raises(git_repo: Path) -> None:
    """An unknown base ref raises a clear resolution error."""
    assert_that(get_changed_files).raises(DiffResolutionError).when_called_with(
        "does-not-exist",
        str(git_repo),
    )


def test_filter_files_by_diff_restricts_to_changed(git_repo: Path) -> None:
    """Filtering keeps only discovered files that changed vs the base."""
    (git_repo / "a.py").write_text("x = 9\n")  # changed
    candidates = [str(git_repo / "a.py"), str(git_repo / "b.py")]

    filtered = filter_files_by_diff(candidates, "main", str(git_repo))

    assert_that(_names(filtered)).is_equal_to(["a.py"])


def test_filter_files_by_diff_matches_through_symlinked_path(
    git_repo: Path,
    tmp_path: Path,
) -> None:
    """A repo reached through a symlink still matches changed files.

    ``git rev-parse --show-toplevel`` realpath's the root while discovery
    preserves symlinks (``os.path.abspath``). Without resolving both sides of
    the membership test, a changed file under a symlinked path is silently
    dropped. Regression test for the ``--diff`` symlink drop.

    Args:
        git_repo: Initialized git repository fixture.
        tmp_path: Pytest temporary directory (the repo itself).
    """
    (git_repo / "a.py").write_text("x = 9\n")  # changed vs main
    link = tmp_path.parent / f"{tmp_path.name}-link"
    link.symlink_to(git_repo, target_is_directory=True)
    # Candidate discovered via the symlink path (symlink preserved).
    candidate = str(link / "a.py")

    filtered = filter_files_by_diff([candidate], "main", str(link))

    assert_that(_names(filtered)).is_equal_to(["a.py"])


def test_filter_files_by_diff_for_paths_matches_through_symlinked_path(
    git_repo: Path,
    tmp_path: Path,
) -> None:
    """Per-repo filtering matches changed files under a symlinked target.

    Exercises both the symlink-aware repo bucketing (``_path_in_repo``) and the
    resolved membership test, since the multi-repo path groups discovered files
    by realpath'd repo root before filtering.

    Args:
        git_repo: Initialized git repository fixture.
        tmp_path: Pytest temporary directory (the repo itself).
    """
    (git_repo / "a.py").write_text("x = 9\n")  # changed vs main
    link = tmp_path.parent / f"{tmp_path.name}-mrlink"
    link.symlink_to(git_repo, target_is_directory=True)
    candidate = str(link / "a.py")

    filtered = filter_files_by_diff_for_paths([candidate], "main", [str(link)])

    assert_that(_names(filtered)).is_equal_to(["a.py"])


def test_filter_files_by_diff_empty_changed_set(git_repo: Path) -> None:
    """With no changes, filtering returns an empty list."""
    candidates = [str(git_repo / "a.py"), str(git_repo / "b.py")]

    filtered = filter_files_by_diff(candidates, "main", str(git_repo))

    assert_that(filtered).is_empty()


def test_walk_files_with_excludes_diff_base(
    git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``walk_files_with_excludes`` restricts discovery to changed files.

    When invoked from inside the repository, diff filtering uses the target
    checkout rather than an unrelated process cwd.
    """
    from lintro.utils.path_filtering import walk_files_with_excludes

    (git_repo / "a.py").write_text("x = 3\n")  # changed
    # b.py unchanged; new untracked d.py added.
    (git_repo / "d.py").write_text("d = 4\n")
    monkeypatch.chdir(git_repo)

    files = walk_files_with_excludes(
        paths=["."],
        file_patterns=["*.py"],
        exclude_patterns=[],
        diff_base="main",
    )

    assert_that(_names(files)).is_equal_to(["a.py", "d.py"])


def test_walk_files_with_excludes_diff_base_outside_process_cwd(
    git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Diff filtering uses the target checkout when cwd is elsewhere."""
    from lintro.utils.path_filtering import walk_files_with_excludes

    (git_repo / "a.py").write_text("x = 3\n")
    (git_repo / "d.py").write_text("d = 4\n")
    outside_cwd = tmp_path / "outside"
    outside_cwd.mkdir()
    monkeypatch.chdir(outside_cwd)

    files = walk_files_with_excludes(
        paths=[str(git_repo)],
        file_patterns=["*.py"],
        exclude_patterns=[],
        diff_base="main",
    )

    assert_that(_names(files)).is_equal_to(["a.py", "d.py"])


def test_walk_files_with_excludes_no_diff_base_scans_all(git_repo: Path) -> None:
    """Without ``diff_base`` all matching files are discovered."""
    from lintro.utils.path_filtering import walk_files_with_excludes

    files = walk_files_with_excludes(
        paths=[str(git_repo)],
        file_patterns=["*.py"],
        exclude_patterns=[],
    )

    assert_that(_names(files)).is_equal_to(["a.py", "b.py"])


def test_diff_default_sentinel_is_distinct() -> None:
    """The sentinel is unlikely to collide with a real ref name."""
    assert_that(DIFF_DEFAULT_SENTINEL).is_not_equal_to("main")
    assert_that(DIFF_DEFAULT_SENTINEL).is_not_equal_to("")


@pytest.fixture
def two_git_repos(tmp_path: Path) -> tuple[Path, Path]:
    """Create two independent git repositories with committed baselines.

    Args:
        tmp_path: Pytest temporary directory.

    Returns:
        Tuple of repository paths, each on a feature branch with ``main`` as
        the default base.
    """
    repos: list[Path] = []
    for name, filename in (("repo_a", "a.py"), ("repo_b", "b.py")):
        repo = tmp_path / name
        repo.mkdir()
        _git(repo, "init", "-q")
        _git(repo, "config", "user.email", "test@example.com")
        _git(repo, "config", "user.name", "Test User")
        (repo / filename).write_text("x = 1\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-qm", "init")
        _git(repo, "branch", "-M", "main")
        _git(repo, "checkout", "-qb", "feature")
        repos.append(repo)
    return repos[0], repos[1]


def test_resolve_git_cwd_from_paths_groups_by_repo(
    two_git_repos: tuple[Path, Path],
) -> None:
    """Each scan target maps to its own repository root."""
    repo_a, repo_b = two_git_repos
    groups = resolve_git_cwd_from_paths([str(repo_a), str(repo_b)])

    assert_that(groups).contains_key(str(repo_a.resolve()))
    assert_that(groups).contains_key(str(repo_b.resolve()))
    assert_that(groups[str(repo_a.resolve())]).is_equal_to([str(repo_a)])
    assert_that(groups[str(repo_b.resolve())]).is_equal_to([str(repo_b)])


def test_resolve_git_cwd_from_paths_non_repo_path(tmp_path: Path) -> None:
    """Paths outside any git repository are grouped under ``None``."""
    plain_dir = tmp_path / "plain"
    plain_dir.mkdir()
    groups = resolve_git_cwd_from_paths([str(plain_dir)])

    assert_that(groups).contains_key(None)
    assert_that(groups[None]).is_equal_to([str(plain_dir)])


def test_get_changed_files_for_paths_multi_repo(
    two_git_repos: tuple[Path, Path],
) -> None:
    """Changed files from every repository are included in the union."""
    repo_a, repo_b = two_git_repos
    (repo_a / "a.py").write_text("x = 42\n")
    _git(repo_a, "commit", "-aqm", "change a")
    (repo_b / "b.py").write_text("y = 99\n")
    _git(repo_b, "commit", "-aqm", "change b")

    changed = get_changed_files_for_paths(
        "main",
        [str(repo_a), str(repo_b)],
    )

    assert_that(_names(changed)).contains("a.py", "b.py")


def test_filter_files_by_diff_for_paths_multi_repo(
    two_git_repos: tuple[Path, Path],
) -> None:
    """Per-repo diff filtering keeps changed files from every repository."""
    repo_a, repo_b = two_git_repos
    (repo_a / "a.py").write_text("x = 42\n")
    _git(repo_a, "commit", "-aqm", "change a")
    (repo_b / "b.py").write_text("y = 99\n")
    _git(repo_b, "commit", "-aqm", "change b")

    candidates = [str(repo_a / "a.py"), str(repo_b / "b.py")]
    filtered = filter_files_by_diff_for_paths(
        candidates,
        "main",
        [str(repo_a), str(repo_b)],
    )

    assert_that(_names(filtered)).contains("a.py", "b.py")


def test_filter_files_by_diff_for_paths_single_repo_regression(
    git_repo: Path,
) -> None:
    """Single-repository behavior matches ``filter_files_by_diff``."""
    (git_repo / "a.py").write_text("x = 9\n")
    candidates = [str(git_repo / "a.py"), str(git_repo / "b.py")]

    legacy = filter_files_by_diff(candidates, "main", str(git_repo))
    grouped = filter_files_by_diff_for_paths(
        candidates,
        "main",
        [str(git_repo)],
    )

    assert_that(_names(grouped)).is_equal_to(_names(legacy))


def test_filter_files_by_diff_for_paths_non_repo_fallback(tmp_path: Path) -> None:
    """Non-repo scan targets keep all discovered files without diff filtering."""
    plain_dir = tmp_path / "plain"
    plain_dir.mkdir()
    plain_file = plain_dir / "plain.py"
    plain_file.write_text("z = 1\n")
    candidates = [str(plain_file)]

    filtered = filter_files_by_diff_for_paths(
        candidates,
        "main",
        [str(plain_dir)],
    )

    assert_that(_names(filtered)).is_equal_to(["plain.py"])


def test_filter_files_by_diff_for_paths_non_repo_dotdot_scan_path(
    tmp_path: Path,
) -> None:
    """Non-repo scan targets containing ``..`` still match discovered files.

    Discovery normalizes ``..`` (``os.path.abspath``), so the scan target must
    be normalized the same way; otherwise a target like ``plain/../plain``
    fails to match and drops the full-scan fallback.

    Args:
        tmp_path: Temporary directory fixture.
    """
    plain_dir = tmp_path / "plain"
    plain_dir.mkdir()
    plain_file = plain_dir / "plain.py"
    plain_file.write_text("z = 1\n")
    candidates = [str(plain_file)]
    dotdot_scan = str(plain_dir / ".." / "plain")

    filtered = filter_files_by_diff_for_paths(
        candidates,
        "main",
        [dotdot_scan],
    )

    assert_that(_names(filtered)).is_equal_to(["plain.py"])


def test_all_repo_defaults_resolvable_multi_repo(
    two_git_repos: tuple[Path, Path],
) -> None:
    """Every grouped repository must resolve a default base."""
    repo_a, repo_b = two_git_repos

    assert_that(
        all_repo_defaults_resolvable([str(repo_a), str(repo_b)]),
    ).is_true()


def test_all_repo_defaults_resolvable_false_for_orphan_branch(
    tmp_path: Path,
) -> None:
    """A repo without ``main``/``master`` fails all-repo default resolution."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    (repo / "only.py").write_text("x = 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "init")
    _git(repo, "branch", "-M", "feature")

    assert_that(all_repo_defaults_resolvable([str(repo)])).is_false()


def test_walk_files_with_excludes_multi_repo_diff(
    two_git_repos: tuple[Path, Path],
) -> None:
    """``walk_files_with_excludes`` includes changed files from each repo."""
    from lintro.utils.path_filtering import walk_files_with_excludes

    repo_a, repo_b = two_git_repos
    (repo_a / "a.py").write_text("x = 42\n")
    _git(repo_a, "commit", "-aqm", "change a")
    (repo_b / "b.py").write_text("y = 99\n")
    _git(repo_b, "commit", "-aqm", "change b")

    files = walk_files_with_excludes(
        paths=[str(repo_a), str(repo_b)],
        file_patterns=["*.py"],
        exclude_patterns=[],
        diff_base="main",
    )

    assert_that(_names(files)).contains("a.py", "b.py")

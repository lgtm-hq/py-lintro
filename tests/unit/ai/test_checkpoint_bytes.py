"""Real Git controls for byte-exact working-tree checkpoints (issue #3116)."""

from __future__ import annotations

import os
import subprocess  # nosec B404 - exercise failing Git plumbing in disposable repos
from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that

from lintro.ai.checkpoints import (
    capture_checkpoint,
    list_checkpoint_refs,
    restore_checkpoint,
)
from tests.conftest import run_git
from tests.unit.conftest import init_git_repo


@pytest.fixture
def byte_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a disposable repository isolated from all ambient Git settings."""
    for name in tuple(os.environ):
        if name.startswith("GIT_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    monkeypatch.setenv("GIT_ATTR_NOSYSTEM", "1")
    repo = init_git_repo(
        tmp_path,
        files={
            "target.txt": "committed\n",
            "deleted.txt": "deleted before capture\n",
            "user.txt": "committed user content\n",
        },
    )
    (repo / "user.txt").write_bytes(b"staged user content\n")
    run_git(["git", "add", "user.txt"], cwd=repo)
    (repo / "user.txt").write_bytes(b"unstaged user content\n")
    return repo


def _configure_conversion(repo: Path, *, autocrlf: str, filtered: bool) -> None:
    """Set only disposable repo config, with an owned harmless clean filter.

    Args:
        repo: Test repository root.
        autocrlf: Git newline conversion setting.
        filtered: Whether attributes select the clean filter.
    """
    run_git(["git", "config", "core.autocrlf", autocrlf], cwd=repo)
    run_git(["git", "config", "core.filemode", "true"], cwd=repo)
    run_git(
        [
            "git",
            "config",
            "filter.checkpoint.clean",
            "printf invoked >> .git/filter-invocations; sed 's/ORIGINAL/CLEANED/g'",
        ],
        cwd=repo,
    )
    (repo / ".gitattributes").write_text(
        "* filter=checkpoint\n" if filtered else "",
        encoding="utf-8",
    )


def _user_state(repo: Path) -> tuple[bytes, bytes, str]:
    """Read index bytes, local config bytes and HEAD without refreshing the index.

    Args:
        repo: Test repository root.

    Returns:
        User Git state that capture and restore must preserve.
    """
    return (
        (repo / ".git/index").read_bytes(),
        (repo / ".git/config").read_bytes(),
        run_git(["git", "rev-parse", "HEAD"], cwd=repo).stdout,
    )


@pytest.mark.parametrize("mode", [0o644, 0o755], ids=["regular", "executable"])
@pytest.mark.parametrize(
    ("autocrlf", "filtered", "original"),
    [
        ("false", False, b"ORIGINAL\n"),
        ("false", False, b"ORIGINAL\r\n"),
        ("input", False, b"ORIGINAL\n"),
        ("input", False, b"ORIGINAL\r\n"),
        ("false", True, b"ORIGINAL\r\n"),
        ("input", True, b"ORIGINAL\x00\xff\r\n"),
    ],
    ids=["ordinary", "raw-crlf", "input-lf", "input-crlf", "clean-filter", "binary"],
)
def test_checkpoint_round_trip_preserves_actual_bytes(
    byte_repo: Path,
    autocrlf: str,
    filtered: bool,
    original: bytes,
    mode: int,
) -> None:
    """Tracked and untracked bytes survive rollback regardless of Git conversion."""
    repo = byte_repo
    _configure_conversion(repo, autocrlf=autocrlf, filtered=filtered)
    targets = [repo / "target.txt", repo / "untracked [1],file.txt"]
    for target in targets:
        target.write_bytes(original)
        target.chmod(mode)
    before = _user_state(repo)
    attributes = (repo / ".gitattributes").read_bytes()

    checkpoint = capture_checkpoint([p.name for p in targets], workspace_root=repo)

    assert checkpoint is not None
    assert_that(_user_state(repo)).is_equal_to(before)
    assert_that((repo / ".gitattributes").read_bytes()).is_equal_to(attributes)
    for target in targets:
        assert_that(target.read_bytes()).is_equal_to(original)
        assert_that(target.stat().st_mode & 0o777).is_equal_to(mode)
        target.write_bytes(b"formatted\n")
        target.chmod(0o644 if mode == 0o755 else 0o755)

    restore_checkpoint(checkpoint)

    assert_that(_user_state(repo)).is_equal_to(before)
    assert_that((repo / ".gitattributes").read_bytes()).is_equal_to(attributes)
    assert_that((repo / "user.txt").read_bytes()).is_equal_to(
        b"unstaged user content\n",
    )
    for target in targets:
        assert_that(target.read_bytes()).is_equal_to(original)
        assert_that(target.stat().st_mode & 0o777).is_equal_to(mode)
    assert_that((repo / ".git/filter-invocations").exists()).is_false()


@pytest.mark.parametrize(
    ("autocrlf", "filtered"),
    [("false", False), ("input", False), ("false", True)],
    ids=["ordinary", "autocrlf", "clean-filter"],
)
def test_checkpoint_keeps_absence_and_symlink_semantics(
    byte_repo: Path,
    autocrlf: str,
    filtered: bool,
) -> None:
    """Missing tracked/new files stay absent and links keep their literal target."""
    repo = byte_repo
    _configure_conversion(repo, autocrlf=autocrlf, filtered=filtered)
    deleted = repo / "deleted.txt"
    deleted.unlink()
    link = repo / "link.txt"
    link.symlink_to("target.txt")
    before = _user_state(repo)

    checkpoint = capture_checkpoint(
        ["deleted.txt", "absent.txt", "link.txt"],
        workspace_root=repo,
    )

    assert checkpoint is not None
    assert_that(_user_state(repo)).is_equal_to(before)
    assert_that(deleted.exists()).is_false()
    assert_that((repo / "absent.txt").exists()).is_false()
    assert_that(os.readlink(link)).is_equal_to("target.txt")
    deleted.write_bytes(b"created by run\n")
    (repo / "absent.txt").write_bytes(b"new file\n")
    link.unlink()
    link.write_bytes(b"replaced by run\n")

    restore_checkpoint(checkpoint)

    assert_that(deleted.exists()).is_false()
    assert_that((repo / "absent.txt").exists()).is_false()
    assert_that(link.is_symlink()).is_true()
    assert_that(os.readlink(link)).is_equal_to("target.txt")
    assert_that((repo / "target.txt").read_bytes()).is_equal_to(b"committed\n")
    assert_that(_user_state(repo)).is_equal_to(before)
    assert_that((repo / ".git/filter-invocations").exists()).is_false()


@pytest.mark.parametrize("command", ["hash-object", "update-index"])
def test_raw_capture_failure_publishes_no_checkpoint(
    byte_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
) -> None:
    """A failing blob/index write leaves user state intact and cleans its index."""
    repo = byte_repo
    original = b"ORIGINAL\r\n"
    (repo / "target.txt").write_bytes(original)
    before = _user_state(repo)
    real_run = subprocess.run
    temporary_indexes: list[Path] = []

    def reject_raw_write(
        args: list[str],
        **kwargs: Any,
    ) -> subprocess.CompletedProcess[Any]:
        if args[1] == command:
            temporary_indexes.append(Path(kwargs["env"]["GIT_INDEX_FILE"]))
            # Drive a real nonzero Git process with the same stdin/environment.
            return real_run([args[0], "lintro-invalid-raw-plumbing"], **kwargs)
        return real_run(args, **kwargs)

    monkeypatch.setattr("lintro.ai.checkpoints.subprocess.run", reject_raw_write)

    checkpoint = capture_checkpoint(["target.txt"], workspace_root=repo)

    assert_that(checkpoint).is_none()
    assert_that(list_checkpoint_refs(workspace_root=repo)).is_empty()
    assert_that(_user_state(repo)).is_equal_to(before)
    assert_that((repo / "target.txt").read_bytes()).is_equal_to(original)
    assert_that(temporary_indexes).is_not_empty()
    for index in temporary_indexes:
        assert_that(index).is_not_equal_to(repo / ".git/index")
        assert_that(index.exists()).is_false()

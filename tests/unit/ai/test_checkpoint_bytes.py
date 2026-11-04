"""Real Git controls for byte-exact working-tree checkpoints (issue #3116)."""

from __future__ import annotations

import os
import subprocess  # nosec B404 - exercise failing Git plumbing in disposable repos
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.ai.checkpoints import (
    Checkpoint,
    CheckpointError,
    capture_checkpoint,
    diff_checkpoint,
    list_checkpoint_refs,
    restore_checkpoint,
)
from lintro.cli import cli
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


@pytest.mark.parametrize(
    ("autocrlf", "filtered", "attributes"),
    [
        ("false", False, ""),
        ("input", False, ""),
        ("input", False, "* text eol=lf\n"),
        ("false", True, "* filter=checkpoint\n"),
    ],
    ids=["ordinary", "autocrlf", "explicit-text", "clean-filter"],
)
def test_diff_compares_raw_bytes_and_only_literal_selected_paths(
    byte_repo: Path,
    autocrlf: str,
    filtered: bool,
    attributes: str,
) -> None:
    """Unchanged raw bytes have no diff; real edits bypass all external drivers."""
    repo = byte_repo
    _configure_conversion(repo, autocrlf=autocrlf, filtered=filtered)
    (repo / ".gitattributes").write_text(attributes, encoding="utf-8")
    run_git(
        ["git", "config", "diff.external", "touch .git/external-diff-invoked"],
        cwd=repo,
    )
    run_git(
        ["git", "config", "diff.checkpoint.textconv", "touch .git/textconv-invoked"],
        cwd=repo,
    )
    with (repo / ".gitattributes").open("a", encoding="utf-8") as config:
        config.write("* diff=checkpoint\n")
    names = ["target.txt", "-dash,é.txt", "line\nbreak.txt", "tab\t[1].txt"]
    for name in names:
        (repo / name).write_bytes(b"ORIGINAL\r\n")
    (repo / "tab\t1.txt").write_bytes(b"PRIVATE_NEIGHBOR\n")
    before = _user_state(repo)
    checkpoint = capture_checkpoint(names, workspace_root=repo)
    assert checkpoint is not None

    assert_that(diff_checkpoint(checkpoint)).is_empty()
    for name in names:
        (repo / name).write_bytes(b"CHANGED\r\n")
    (repo / "tab\t1.txt").write_bytes(b"PRIVATE_EDIT\n")
    diff = diff_checkpoint(checkpoint)

    assert_that(diff.count("-ORIGINAL\r\n")).is_equal_to(len(names))
    assert_that(diff.count("+CHANGED\r\n")).is_equal_to(len(names))
    assert_that(diff).does_not_contain("CLEANED", "PRIVATE_NEIGHBOR", "PRIVATE_EDIT")
    assert_that(diff_checkpoint(checkpoint, ["user.txt"])).is_empty()
    subset = diff_checkpoint(checkpoint, ["tab\t[1].txt"])
    assert_that(subset.count("diff --git")).is_equal_to(1)
    assert_that(subset).contains("-ORIGINAL\r\n", "+CHANGED\r\n")
    restore_checkpoint(checkpoint)
    assert_that(diff_checkpoint(checkpoint)).is_empty()
    assert_that(_user_state(repo)).is_equal_to(before)
    assert_that((repo / ".git/filter-invocations").exists()).is_false()
    assert_that((repo / ".git/external-diff-invoked").exists()).is_false()
    assert_that((repo / ".git/textconv-invoked").exists()).is_false()


@pytest.mark.parametrize("file_to_directory", [True, False])
def test_capture_replaces_conflicting_head_entries(
    byte_repo: Path,
    file_to_directory: bool,
) -> None:
    """Capture supports both HEAD shape transitions and restores captured files."""
    repo = byte_repo
    parent = repo / "shape"
    child = parent / "child.txt"
    if file_to_directory:
        parent.write_bytes(b"HEAD file\n")
    else:
        parent.mkdir()
        child.write_bytes(b"HEAD child\n")
    run_git(["git", "add", "shape"], cwd=repo)
    run_git(["git", "commit", "-m", "seed shape"], cwd=repo)
    if file_to_directory:
        parent.unlink()
        parent.mkdir()
        target = child
    else:
        child.unlink()
        parent.rmdir()
        target = parent
    target.write_bytes(b"CAPTURED\r\n")
    target.chmod(0o755)
    before = _user_state(repo)

    checkpoint = capture_checkpoint(
        [target.relative_to(repo).as_posix()],
        workspace_root=repo,
    )

    assert checkpoint is not None
    assert_that(_user_state(repo)).is_equal_to(before)
    assert_that(diff_checkpoint(checkpoint)).is_empty()
    target.write_bytes(b"formatted\n")
    target.chmod(0o644)
    assert_that(diff_checkpoint(checkpoint)).contains("-CAPTURED\r\n", "+formatted\n")
    restore_checkpoint(checkpoint)
    assert_that(target.read_bytes()).is_equal_to(b"CAPTURED\r\n")
    assert_that(target.stat().st_mode & 0o777).is_equal_to(0o755)
    assert_that(_user_state(repo)).is_equal_to(before)


@pytest.mark.parametrize("via_fmt", [False, True], ids=["capture", "public-fmt"])
def test_unreadable_capture_is_fail_soft_and_cleans_index(
    byte_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    via_fmt: bool,
) -> None:
    """A real read denial returns None and public fmt completes without a traceback."""
    repo = byte_repo
    target = repo / "target.txt"
    original = target.read_bytes()
    (repo / ".lintro-config.yaml").write_text(
        "ai:\n  enabled: false\n  checkpoint_fmt: true\n",
        encoding="utf-8",
    )
    (repo / "ready.py").write_text("ready = 1\n", encoding="utf-8")
    before = _user_state(repo)
    indexes: list[Path] = []
    real_mkstemp = tempfile.mkstemp

    def record_index(*args: Any, **kwargs: Any) -> tuple[int, str]:
        fd, name = real_mkstemp(*args, **kwargs)
        if kwargs.get("prefix", "").startswith("lintro-git-"):
            indexes.append(Path(name))
        return fd, name

    monkeypatch.setattr(tempfile, "mkstemp", record_index)
    monkeypatch.chdir(repo)
    target.chmod(0o000)
    try:
        with pytest.raises(PermissionError):
            target.read_bytes()
        if via_fmt:
            result = CliRunner().invoke(
                cli,
                ["fmt", ".", "--tools", "ruff", "--no-art"],
            )
            assert_that(result.exception).described_as(result.output).is_none()
            assert_that(result.exit_code).is_equal_to(0)
            assert_that(result.output).does_not_contain(
                "Captured fmt checkpoint",
                "Traceback",
            )
        else:
            assert_that(
                capture_checkpoint(["target.txt"], workspace_root=repo),
            ).is_none()
        assert_that(target.stat().st_mode & 0o777).is_equal_to(0)
    finally:
        target.chmod(0o644)

    assert_that(target.read_bytes()).is_equal_to(original)
    assert_that((repo / "ready.py").read_bytes()).is_equal_to(b"ready = 1\n")
    assert_that(_user_state(repo)).is_equal_to(before)
    assert_that(list_checkpoint_refs(workspace_root=repo)).is_empty()
    assert_that(indexes).is_not_empty()
    for index in indexes:
        assert_that(index.exists()).is_false()
        assert_that(Path(f"{index}.lock").exists()).is_false()


@pytest.mark.parametrize("filemode", ["true", "false"])
def test_capture_restores_actual_mode_even_when_git_ignores_mode_changes(
    byte_repo: Path,
    filemode: str,
) -> None:
    """Rollback preserves the captured filesystem mode rather than HEAD's mode."""
    repo = byte_repo
    target = repo / "target.txt"
    target.chmod(0o755)
    run_git(["git", "config", "core.filemode", "true"], cwd=repo)
    run_git(["git", "add", "target.txt"], cwd=repo)
    run_git(["git", "commit", "-m", "seed executable"], cwd=repo)
    run_git(["git", "config", "core.filemode", filemode], cwd=repo)
    target.chmod(0o644)
    before = _user_state(repo)

    checkpoint = capture_checkpoint(["target.txt"], workspace_root=repo)

    assert checkpoint is not None
    target.chmod(0o755)
    restore_checkpoint(checkpoint)
    assert_that(target.stat().st_mode & 0o777).is_equal_to(0o644)
    assert_that(_user_state(repo)).is_equal_to(before)


def test_capture_batches_real_git_processes(
    byte_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Many real target files retain distinct bytes without per-file Git spawns."""
    repo = byte_repo
    names = [f"batch-{number:04}.txt" for number in range(503)]
    for number, name in enumerate(names):
        (repo / name).write_bytes(f"raw {number}\r\n".encode())
    before = _user_state(repo)
    commands: Counter[str] = Counter()
    real_run = subprocess.run

    def count_git(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        commands[args[1]] += 1
        return real_run(args, **kwargs)

    with monkeypatch.context() as counting:
        counting.setattr("lintro.ai.checkpoints.subprocess.run", count_git)
        started = time.perf_counter()
        checkpoint = capture_checkpoint(names, workspace_root=repo)
        elapsed = time.perf_counter() - started

    print(
        f"public capture: files={len(names)} processes={sum(commands.values())} seconds={elapsed:.6f} commands={dict(commands)}",
    )
    assert checkpoint is not None
    assert_that(_user_state(repo)).is_equal_to(before)
    assert_that(sum(commands.values())).is_less_than(25)
    for number in (0, 499, 502):
        (repo / names[number]).write_bytes(b"changed\n")
    restore_checkpoint(checkpoint, [names[number] for number in (0, 499, 502)])
    for number in (0, 499, 502):
        assert_that((repo / names[number]).read_bytes()).is_equal_to(
            f"raw {number}\r\n".encode(),
        )


@pytest.mark.parametrize("operation", ["stat", "readlink"])
def test_capture_filesystem_oserror_keeps_user_state(
    byte_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    """Filesystem metadata/link failures abort capture and remove its temp index."""
    repo = byte_repo
    target = repo / "target.txt"
    if operation == "readlink":
        target = repo / "link.txt"
        target.symlink_to("target.txt")
    before = _user_state(repo)
    real_run = subprocess.run
    real_stat = Path.stat
    real_readlink = os.readlink
    indexes: list[Path] = []

    def observe_index(
        args: list[str],
        **kwargs: Any,
    ) -> subprocess.CompletedProcess[Any]:
        result = real_run(args, **kwargs)
        if args[1] == "read-tree":
            indexes.append(Path(kwargs["env"]["GIT_INDEX_FILE"]))
        return result

    def deny_stat(path: Path, *args: Any, **kwargs: Any) -> os.stat_result:
        if indexes and path == target:
            raise PermissionError("owned stat failure")
        return real_stat(path, *args, **kwargs)

    def deny_readlink(path: Any, *args: Any, **kwargs: Any) -> Any:
        if indexes and Path(path) == target:
            raise OSError("owned readlink failure")
        return real_readlink(path, *args, **kwargs)

    with monkeypatch.context() as failing:
        failing.setattr("lintro.ai.checkpoints.subprocess.run", observe_index)
        if operation == "stat":
            failing.setattr(Path, "stat", deny_stat)
            failing.setattr(Path, "lstat", deny_stat)
        else:
            failing.setattr(os, "readlink", deny_readlink)
        checkpoint = capture_checkpoint([target.name], workspace_root=repo)

    assert_that(checkpoint).is_none()
    assert_that(list_checkpoint_refs(workspace_root=repo)).is_empty()
    assert_that(_user_state(repo)).is_equal_to(before)
    assert_that((repo / "target.txt").read_bytes()).is_equal_to(b"committed\n")
    if operation == "readlink":
        assert_that(os.readlink(target)).is_equal_to("target.txt")
    assert_that(indexes).is_not_empty()
    for index in indexes:
        assert_that(index.exists()).is_false()


def test_public_fmt_captures_raw_bytes_from_project_checkpoint_option(
    byte_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real fmt invocation honors the raw config mapping and records pre-run bytes."""
    repo = byte_repo
    (repo / ".lintro-config.yaml").write_text(
        "ai:\n  enabled: false\n  checkpoint_fmt: true\n",
        encoding="utf-8",
    )
    target = repo / "ready.py"
    original = b"ready=1\r\n"
    target.write_bytes(original)
    before = _user_state(repo)
    monkeypatch.chdir(repo)

    result = CliRunner().invoke(
        cli,
        ["fmt", "ready.py", "--tools", "black", "--no-art"],
    )

    assert_that(result.exception).described_as(result.output).is_none()
    assert_that(result.exit_code).is_equal_to(0)
    refs = list_checkpoint_refs(workspace_root=repo)
    assert_that(refs).is_length(1)
    assert_that(result.output).contains("Captured fmt checkpoint", refs[0])
    assert_that(target.read_bytes()).is_equal_to(b"ready = 1\r\n")
    restore_checkpoint(
        Checkpoint(
            ref=refs[0],
            run_id=refs[0].rsplit("/", 1)[1],
            root=repo,
            paths=("ready.py",),
        ),
    )
    assert_that(target.read_bytes()).is_equal_to(original)
    assert_that(_user_state(repo)).is_equal_to(before)


@pytest.mark.parametrize("command", ["hash-object", "update-index", "diff"])
def test_raw_diff_failure_raises_and_cleans_private_index(
    byte_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
) -> None:
    """A real failing Git diff preparation cannot be mistaken for an empty diff."""
    repo = byte_repo
    checkpoint = capture_checkpoint(["target.txt"], workspace_root=repo)
    assert checkpoint is not None
    (repo / "target.txt").write_bytes(b"changed\r\n")
    before = _user_state(repo)
    real_run = subprocess.run
    indexes: list[Path] = []

    def reject_diff(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        if args[1] == "read-tree":
            indexes.append(Path(kwargs["env"]["GIT_INDEX_FILE"]))
        if args[1] == command:
            return real_run([args[0], "lintro-invalid-diff-plumbing"], **kwargs)
        return real_run(args, **kwargs)

    with monkeypatch.context() as failing:
        failing.setattr("lintro.ai.checkpoints.subprocess.run", reject_diff)
        with pytest.raises(CheckpointError):
            diff_checkpoint(checkpoint)

    assert_that(_user_state(repo)).is_equal_to(before)
    assert_that(list_checkpoint_refs(workspace_root=repo)).is_equal_to([checkpoint.ref])
    assert_that((repo / "target.txt").read_bytes()).is_equal_to(b"changed\r\n")
    assert_that(indexes).is_not_empty()
    for index in indexes:
        assert_that(index.exists()).is_false()
        assert_that(Path(f"{index}.lock").exists()).is_false()

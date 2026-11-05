"""Real Git controls for byte-exact working-tree checkpoints (issue #3116)."""

from __future__ import annotations

import errno
import os
import shutil
import subprocess  # nosec B404 - exercise failing Git plumbing in disposable repos
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.ai import checkpoints
from lintro.ai.checkpoints import (
    Checkpoint,
    CheckpointError,
    capture_checkpoint,
    diff_checkpoint,
    list_checkpoint_refs,
    restore_checkpoint,
)
from lintro.cli import cli
from lintro.utils.tool_executor import execute_run
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


@pytest.mark.parametrize("command", ["hash-object", "update-index", "hash-count"])
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
        if command == "hash-count" and args[1] == "hash-object":
            temporary_indexes.append(Path(kwargs["env"]["GIT_INDEX_FILE"]))
            result = real_run(args, **kwargs)
            result.stdout = b""
            return result
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

    real_os_stat = os.stat

    def _is_target(path: Any, kwargs: dict[str, Any]) -> bool:
        dir_fd = kwargs.get("dir_fd")
        if dir_fd is None:
            return Path(path) == target
        # Descriptor-relative calls name only the final component, so the
        # descriptor must also be the target's own parent directory.
        return path == target.name and os.path.samestat(
            os.fstat(dir_fd),
            real_os_stat(target.parent),
        )

    def deny_stat(path: Path, *args: Any, **kwargs: Any) -> os.stat_result:
        if indexes and path == target:
            raise PermissionError("owned stat failure")
        return real_stat(path, *args, **kwargs)

    def deny_os_stat(path: Any, *args: Any, **kwargs: Any) -> os.stat_result:
        if indexes and _is_target(path, kwargs):
            raise PermissionError("owned stat failure")
        return real_os_stat(path, *args, **kwargs)

    def deny_readlink(path: Any, *args: Any, **kwargs: Any) -> Any:
        if indexes and _is_target(path, kwargs):
            raise OSError("owned readlink failure")
        return real_readlink(path, *args, **kwargs)

    with monkeypatch.context() as failing:
        failing.setattr("lintro.ai.checkpoints.subprocess.run", observe_index)
        if operation == "stat":
            failing.setattr(Path, "stat", deny_stat)
            failing.setattr(Path, "lstat", deny_stat)
            failing.setattr(os, "stat", deny_os_stat)
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


@pytest.mark.parametrize("checkpoint_fmt", [False, True])
def test_public_fmt_captures_raw_bytes_from_project_checkpoint_option(
    byte_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    checkpoint_fmt: bool,
) -> None:
    """A real fmt invocation honors the raw config mapping and records pre-run bytes."""
    repo = byte_repo
    (repo / ".lintro-config.yaml").write_text(
        f"ai:\n  enabled: false\n  checkpoint_fmt: {str(checkpoint_fmt).lower()}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LINTRO_AI_PROVIDER", "anthropic")
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
    assert_that(target.read_bytes()).is_equal_to(b"ready = 1\r\n")
    if checkpoint_fmt:
        assert_that(refs).is_length(1)
        assert_that(result.output).contains("Captured fmt checkpoint", refs[0])
        restore_checkpoint(
            Checkpoint(
                ref=refs[0],
                run_id=refs[0].rsplit("/", 1)[1],
                root=repo,
                paths=("ready.py",),
            ),
        )
        assert_that(target.read_bytes()).is_equal_to(original)
    else:
        assert_that(refs).is_empty()
        assert_that(result.output).does_not_contain("Captured fmt checkpoint")
    assert_that(_user_state(repo)).is_equal_to(before)


@pytest.mark.parametrize(
    "ai_section",
    ["{}\n", "ai:\n  enabled: false\n"],
    ids=["default", "disabled"],
)
@pytest.mark.parametrize(
    ("variable", "value"),
    [("LINTRO_AI_PROVIDER", "bogus"), ("LINTRO_AI_PROVIDERS__BOGUS__X", "1")],
)
def test_public_fmt_invalid_ai_override_is_usage_error_before_formatting(
    byte_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    ai_section: str,
    variable: str,
    value: str,
) -> None:
    """Default and disabled AI reject invalid overrides before any formatter runs."""
    repo = byte_repo
    (repo / ".lintro-config.yaml").write_text(ai_section, encoding="utf-8")
    target = repo / "ready.py"
    original = b"ready=1\r\n"
    target.write_bytes(original)
    before = _user_state(repo)
    monkeypatch.chdir(repo)
    monkeypatch.setenv(variable, value)

    with patch("lintro.api.pipeline.execute_run", wraps=execute_run) as execution:
        result = CliRunner().invoke(
            cli,
            ["fmt", "ready.py", "--tools", "black", "--no-art"],
        )

    assert_that(result.exit_code).described_as(repr(result.exception)).is_equal_to(2)
    assert_that(result.output).contains("Error:", variable).does_not_contain(
        "Traceback",
    )
    assert_that(target.read_bytes()).is_equal_to(original)
    assert_that(_user_state(repo)).is_equal_to(before)
    assert_that(list_checkpoint_refs(workspace_root=repo)).is_empty()
    execution.assert_not_called()


@pytest.mark.parametrize("boundary", ["capture", "diff"])
@pytest.mark.parametrize("deleted", [False, True], ids=["regular", "deleted"])
def test_long_path_arguments_fit_constrained_exec_budget(
    byte_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
    deleted: bool,
) -> None:
    """Real Git survives an artificial 64 KiB exec budget, not native macOS testing."""
    repo = byte_repo
    prefix = Path("-" + "é" * 90) / ("界" * 60) / ("ø" * 90)
    names = [
        (prefix / f"file-{number:04}\t\n[1].txt").as_posix() for number in range(503)
    ]
    for number, name in enumerate(names):
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(f"ORIGINAL {number}\r\n".encode())
    run_git(["git", "add", "--", prefix.as_posix()], cwd=repo)
    run_git(["git", "commit", "-m", "seed long paths"], cwd=repo)
    _configure_conversion(repo, autocrlf="input", filtered=True)
    checkpoint = None
    if boundary == "diff":
        checkpoint = capture_checkpoint(names, workspace_root=repo)
        assert checkpoint is not None
    if deleted:
        for name in names:
            (repo / name).unlink()
    before = _user_state(repo)
    budget = 64 * 1024
    with _constrained_git_exec(monkeypatch, budget=budget) as (
        commands,
        sizes,
        indexes,
    ):
        if boundary == "capture":
            checkpoint = capture_checkpoint(names, workspace_root=repo)
            assert checkpoint is not None
        else:
            assert checkpoint is not None
            diff = diff_checkpoint(checkpoint)
            assert_that(diff.count("-ORIGINAL")).is_equal_to(
                len(names) if deleted else 0,
            )
            assert_that(bool(diff)).is_equal_to(deleted)

    print(
        f"artificial exec budget={budget} boundary={boundary} deleted={deleted} max_bytes={max(sizes)} processes={sum(commands.values())} commands={dict(commands)}",
    )
    assert_that(max(sizes)).is_less_than_or_equal_to(budget)
    assert_that(sum(commands.values())).is_less_than(60)
    assert_that(_user_state(repo)).is_equal_to(before)
    for index in indexes:
        assert_that(index.exists()).is_false()
        assert_that(Path(f"{index}.lock").exists()).is_false()
    if boundary == "capture":
        for name in names:
            (repo / name).write_bytes(b"changed\n")
        restore_checkpoint(checkpoint)
        for number, name in enumerate(names):
            if deleted:
                assert_that((repo / name).exists()).is_false()
            else:
                assert_that((repo / name).read_bytes()).is_equal_to(
                    f"ORIGINAL {number}\r\n".encode(),
                )
    assert_that(_user_state(repo)).is_equal_to(before)
    assert_that((repo / ".git/filter-invocations").exists()).is_false()


def test_constrained_exec_isolates_oversized_ambient_environment(
    byte_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The artificial limit applies to children without changing ambient state."""
    budget = 64 * 1024
    original = os.environ.copy()
    before = _user_state(byte_repo)
    with monkeypatch.context() as ambient:
        ambient.setenv("LINTRO_TEST_EXEC_PADDING", "x" * (100 * 1024))
        oversized = os.environ.copy()
        effective = checkpoints._git_env(None)
        assert_that(len(oversized["LINTRO_TEST_EXEC_PADDING"])).is_greater_than(budget)
        with _constrained_git_exec(ambient, budget=budget) as (_, sizes, indexes):
            checkpoint = capture_checkpoint(["target.txt"], workspace_root=byte_repo)
            assert checkpoint is not None
            assert_that(diff_checkpoint(checkpoint)).is_empty()
            assert_that(dict(os.environ)).is_equal_to(oversized)
        assert_that(dict(os.environ)).is_equal_to(oversized)
        assert_that(checkpoints._git_env(None)).is_equal_to(effective)

    assert_that(dict(os.environ)).is_equal_to(original)
    assert_that(max(sizes)).is_less_than_or_equal_to(budget)
    assert_that(_user_state(byte_repo)).is_equal_to(before)
    assert_that(indexes).is_not_empty()
    for index in indexes:
        assert_that(index.exists()).is_false()
        assert_that(Path(f"{index}.lock").exists()).is_false()


@contextmanager
def _constrained_git_exec(
    monkeypatch: pytest.MonkeyPatch,
    *,
    budget: int,
) -> Iterator[tuple[Counter[str], list[int], set[Path]]]:
    """Enforce an artificial exec limit while delegating valid calls to real Git.

    Args:
        monkeypatch: Scoped patch facility.
        budget: Combined argv/environment byte ceiling, including pointers.

    Yields:
        tuple[Counter[str], list[int], set[Path]]: Observed commands, encoded
            exec sizes, and temporary index paths.
    """
    real_run = subprocess.run
    real_sysconf = os.sysconf
    commands: Counter[str] = Counter()
    sizes: list[int] = []
    indexes: set[Path] = set()

    def constrained_run(
        args: list[str],
        **kwargs: Any,
    ) -> subprocess.CompletedProcess[Any]:
        environment = kwargs["env"]
        size = sum(len(os.fsencode(arg)) + 1 + 8 for arg in args)
        size += sum(
            len(os.fsencode(key)) + len(os.fsencode(value)) + 2 + 8
            for key, value in environment.items()
        )
        sizes.append(size)
        commands[args[1]] += 1
        if "GIT_INDEX_FILE" in environment:
            indexes.add(Path(environment["GIT_INDEX_FILE"]))
        if size > budget:
            raise OSError(errno.E2BIG, f"artificial exec budget {budget}; got {size}")
        return real_run(args, **kwargs)

    with monkeypatch.context() as limited:
        # Hosted CI's inherited environment can itself exceed this artificial
        # ceiling. Give both the production batcher and subprocess adapter the
        # same deterministic child environment without changing os.environ.
        # Inherited ``GIT_*`` variables stay in the base so production's own
        # scrubbing, not this fixture, decides which of them reach Git.
        base = {
            **{
                key: value
                for key, value in os.environ.items()
                if key.startswith("GIT_")
            },
            "PATH": os.defpath,
            "LC_ALL": "C",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_ATTR_NOSYSTEM": "1",
        }
        limited.setattr(
            "lintro.ai.checkpoints._git_env",
            lambda overlay: checkpoints._scrub_git_env(base, overlay),
        )
        # Advertise the same deterministic limit that constrained_run enforces.
        limited.setattr(
            os,
            "sysconf",
            lambda key: budget if key == "SC_ARG_MAX" else real_sysconf(key),
        )
        limited.setattr("lintro.ai.checkpoints.subprocess.run", constrained_run)
        yield commands, sizes, indexes


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


_BEYOND_LINK = b"bytes reached only through a symlinked parent\n"


def _linked_destination(
    repo: Path,
    tmp_path_factory: pytest.TempPathFactory,
    escape: str,
) -> Path:
    """Create the directory a swapped-in parent symlink will point at.

    Args:
        repo: Test repository root.
        tmp_path_factory: Source of directories outside the repository.
        escape: ``outside`` for a foreign directory, ``inside`` for an
            untracked directory in the same work tree.

    Returns:
        The destination directory, holding ``f.txt`` with unique bytes.
    """
    if escape == "outside":
        destination = tmp_path_factory.mktemp("outside-repo")
    else:
        destination = repo / "elsewhere"
        destination.mkdir()
    (destination / "f.txt").write_bytes(_BEYOND_LINK)
    return destination


def _replace_with_symlink(directory: Path, destination: Path) -> None:
    """Swap a real target directory for a symlink to ``destination``.

    Args:
        directory: Directory to remove (its files are deleted first).
        destination: Directory the new symlink points at.
    """
    for child in directory.iterdir():
        child.unlink()
    directory.rmdir()
    directory.symlink_to(destination, target_is_directory=True)


def _object_exists(repo: Path, data: bytes) -> bool:
    """Return whether a blob holding ``data`` was written to the object store.

    Args:
        repo: Test repository root.
        data: Exact blob contents.

    Returns:
        True when the repository has that blob object.
    """
    with tempfile.NamedTemporaryFile(delete=False) as probe:
        probe.write(data)
    try:
        sha = run_git(
            ["git", "hash-object", "--no-filters", probe.name],
            cwd=repo,
        ).stdout.strip()
    finally:
        Path(probe.name).unlink()
    try:
        run_git(["git", "cat-file", "-e", sha], cwd=repo)
    except subprocess.CalledProcessError:
        return False
    return True


@pytest.mark.parametrize("escape", ["outside", "inside"])
def test_capture_refuses_target_beyond_symlinked_parent(
    byte_repo: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    escape: str,
) -> None:
    """A parent that becomes a symlink after path checks fails capture closed."""
    repo = byte_repo
    (repo / "sub").mkdir()
    (repo / "sub/f.txt").write_bytes(b"captured\n")
    destination = _linked_destination(repo, tmp_path_factory, escape)
    before = _user_state(repo)
    real_normalize = checkpoints._normalize_paths

    def normalize_then_swap(*args: Any, **kwargs: Any) -> list[str]:
        rels = real_normalize(*args, **kwargs)
        _replace_with_symlink(repo / "sub", destination)
        return rels

    monkeypatch.setattr(checkpoints, "_normalize_paths", normalize_then_swap)

    checkpoint = capture_checkpoint(["target.txt", "sub/f.txt"], workspace_root=repo)

    assert_that(checkpoint).is_none()
    assert_that((repo / "sub").is_symlink()).is_true()
    assert_that(list_checkpoint_refs(workspace_root=repo)).is_empty()
    assert_that(_object_exists(repo, _BEYOND_LINK)).is_false()
    assert_that((destination / "f.txt").read_bytes()).is_equal_to(_BEYOND_LINK)
    assert_that(_user_state(repo)).is_equal_to(before)


@pytest.mark.parametrize("escape", ["outside", "inside"])
def test_rollback_never_crosses_parent_swapped_for_symlink(
    byte_repo: Path,
    tmp_path_factory: pytest.TempPathFactory,
    escape: str,
) -> None:
    """Diff reads nothing and restore writes nothing beyond a swapped-in link."""
    repo = byte_repo
    (repo / "zz").mkdir()
    (repo / "zz/f.txt").write_bytes(b"captured\n")
    checkpoint = capture_checkpoint(["target.txt", "zz/f.txt"], workspace_root=repo)
    assert checkpoint is not None
    destination = _linked_destination(repo, tmp_path_factory, escape)
    _replace_with_symlink(repo / "zz", destination)
    (repo / "target.txt").write_bytes(b"edited after capture\n")
    before = _user_state(repo)

    diff = diff_checkpoint(checkpoint)

    # Git's own working-tree view: nothing exists beyond a symbolic link.
    assert_that(diff).contains("deleted file mode 100644", "-captured")
    assert_that(diff).does_not_contain(_BEYOND_LINK.decode())
    assert_that(_object_exists(repo, _BEYOND_LINK)).is_false()
    with pytest.raises(CheckpointError, match="symbolic link"):
        restore_checkpoint(checkpoint)
    # Refused before the write phase: the earlier target is not rolled back.
    assert_that((repo / "target.txt").read_bytes()).is_equal_to(
        b"edited after capture\n",
    )
    assert_that((destination / "f.txt").read_bytes()).is_equal_to(_BEYOND_LINK)
    assert_that(sorted(p.name for p in destination.iterdir())).is_equal_to(["f.txt"])
    assert_that((repo / "zz").is_symlink()).is_true()
    assert_that(list_checkpoint_refs(workspace_root=repo)).is_equal_to([checkpoint.ref])
    assert_that(_user_state(repo)).is_equal_to(before)


@pytest.mark.parametrize(
    ("filemode", "mode_changes", "created_mode"),
    [("true", 2, "100755"), ("false", 0, "100644")],
)
def test_diff_honours_core_filemode_for_mode_only_changes(
    byte_repo: Path,
    filemode: str,
    mode_changes: int,
    created_mode: str,
) -> None:
    """``core.filemode=false`` hides executable-bit changes, as ``git diff`` does."""
    repo = byte_repo
    run_git(["git", "config", "core.filemode", filemode], cwd=repo)
    target = repo / "target.txt"
    untracked = repo / "untracked.txt"
    untracked.write_bytes(b"untracked\n")
    for path in (target, untracked):
        path.chmod(0o644)
    later = repo / "later.txt"
    checkpoint = capture_checkpoint(
        ["target.txt", "untracked.txt", "later.txt"],
        workspace_root=repo,
    )
    assert checkpoint is not None
    before = _user_state(repo)
    for path in (target, untracked):
        path.chmod(0o755)

    mode_only = diff_checkpoint(checkpoint)
    target.write_bytes(b"committed\nedited\n")
    later.write_bytes(b"created after capture\n")
    later.chmod(0o755)
    with_content = diff_checkpoint(checkpoint)

    assert_that(mode_only.count("old mode 100644\nnew mode 100755")).is_equal_to(
        mode_changes,
    )
    assert_that(bool(mode_only)).is_equal_to(bool(mode_changes))
    assert_that(with_content.count("new mode 100755")).is_equal_to(mode_changes)
    assert_that(with_content).contains(
        "+edited",
        f"new file mode {created_mode}\n",
        "+created after capture",
    )
    restore_checkpoint(checkpoint)
    for path in (target, untracked):
        assert_that(path.stat().st_mode & 0o777).is_equal_to(0o644)
    assert_that(target.read_bytes()).is_equal_to(b"committed\n")
    assert_that(later.exists()).is_false()
    assert_that(_user_state(repo)).is_equal_to(before)


def test_constrained_exec_scrubs_inherited_git_location(
    byte_repo: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hook-exported repository variables never reach constrained Git children."""
    repo = byte_repo
    decoy = init_git_repo(tmp_path_factory.mktemp("decoy"), files={"d.txt": "d\n"})
    sentinel_index = decoy / "sentinel-index"
    decoy_state = _user_state(decoy)
    before = _user_state(repo)
    monkeypatch.setenv("GIT_DIR", str(decoy / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(decoy))
    monkeypatch.setenv("GIT_INDEX_FILE", str(sentinel_index))

    with _constrained_git_exec(monkeypatch, budget=64 * 1024) as (_, _, indexes):
        checkpoint = capture_checkpoint(["target.txt"], workspace_root=repo)
        assert checkpoint is not None
        (repo / "target.txt").write_bytes(b"changed\n")
        assert_that(diff_checkpoint(checkpoint)).contains("+changed")
        restore_checkpoint(checkpoint)

    assert_that(checkpoint.root).is_equal_to(repo.resolve())
    assert_that(indexes).is_not_empty().does_not_contain(sentinel_index)
    assert_that(sentinel_index.exists()).is_false()
    assert_that((repo / "target.txt").read_bytes()).is_equal_to(b"committed\n")
    # The checks below run real Git outside the scrubbing fixture; with these
    # still exported they would inspect the decoy instead of ``repo``.
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(name)
    assert_that(list_checkpoint_refs(workspace_root=repo)).is_equal_to([checkpoint.ref])
    assert_that(list_checkpoint_refs(workspace_root=decoy)).is_empty()
    assert_that(_user_state(decoy)).is_equal_to(decoy_state)
    assert_that(_user_state(repo)).is_equal_to(before)


_DIR_FD_ONLY = pytest.mark.skipif(
    os.open not in os.supports_dir_fd,
    reason="descriptor-relative access needs dir_fd support",
)


def _checkpoint_blob(repo: Path, checkpoint: Checkpoint, rel: str) -> bytes:
    """Return the bytes a checkpoint recorded for ``rel``.

    Args:
        repo: Test repository root.
        checkpoint: Captured checkpoint.
        rel: Repo-relative path inside the checkpoint tree.

    Returns:
        The recorded blob contents.
    """
    return subprocess.run(  # nosec B603 B607 - fixed git argv in a disposable repo
        ["git", "cat-file", "-p", f"{checkpoint.tree_sha}:{rel}"],
        cwd=repo,
        capture_output=True,
        check=True,
    ).stdout


@_DIR_FD_ONLY
@pytest.mark.parametrize("boundary", ["capture", "diff"])
@pytest.mark.parametrize("escape", ["outside", "inside"])
def test_parent_swapped_after_mode_check_never_redirects_read(
    byte_repo: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
    escape: str,
) -> None:
    """A parent swapped for a link after the symlink check cannot supply bytes."""
    repo = byte_repo
    (repo / "sub").mkdir()
    (repo / "sub/f.txt").write_bytes(b"captured\n")
    destination = _linked_destination(repo, tmp_path_factory, escape)
    checkpoint = None
    if boundary == "diff":
        checkpoint = capture_checkpoint(["sub/f.txt"], workspace_root=repo)
        assert checkpoint is not None
    before = _user_state(repo)
    real_target_modes = checkpoints._target_modes
    swaps: list[Path] = []

    def check_then_swap(*args: Any, **kwargs: Any) -> Any:
        modes = real_target_modes(*args, **kwargs)
        _replace_with_symlink(repo / "sub", destination)
        swaps.append(repo / "sub")
        return modes

    monkeypatch.setattr(checkpoints, "_target_modes", check_then_swap)
    if boundary == "capture":
        checkpoint = capture_checkpoint(["sub/f.txt"], workspace_root=repo)
        assert checkpoint is not None
    else:
        assert checkpoint is not None
        diff = diff_checkpoint(checkpoint)
        assert_that(diff).does_not_contain(_BEYOND_LINK.decode())

    assert_that(swaps).is_length(1)
    assert_that((repo / "sub").is_symlink()).is_true()
    # The bytes are the ones present when the parent was checked.
    assert_that(_checkpoint_blob(repo, checkpoint, "sub/f.txt")).is_equal_to(
        b"captured\n",
    )
    assert_that(_object_exists(repo, _BEYOND_LINK)).is_false()
    assert_that((destination / "f.txt").read_bytes()).is_equal_to(_BEYOND_LINK)
    assert_that(_user_state(repo)).is_equal_to(before)


@_DIR_FD_ONLY
def test_capture_reads_through_the_pinned_parent_descriptor(
    byte_repo: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Swapping a parent after its descriptor is opened cannot redirect the read."""
    repo = byte_repo
    (repo / "sub").mkdir()
    (repo / "sub/f.txt").write_bytes(b"captured\n")
    destination = _linked_destination(repo, tmp_path_factory, "outside")
    real_open = os.open
    swaps: list[int] = []

    def open_then_swap(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        descriptor = real_open(path, flags, *args, **kwargs)
        if path == "sub" and kwargs.get("dir_fd") is not None and not swaps:
            (repo / "sub").rename(repo / "sub-moved")
            (repo / "sub").symlink_to(destination, target_is_directory=True)
            swaps.append(descriptor)
        return descriptor

    with monkeypatch.context() as racing:
        racing.setattr(os, "open", open_then_swap)
        checkpoint = capture_checkpoint(["sub/f.txt"], workspace_root=repo)

    assert checkpoint is not None
    assert_that(swaps).is_length(1)
    assert_that((repo / "sub").is_symlink()).is_true()
    assert_that(_checkpoint_blob(repo, checkpoint, "sub/f.txt")).is_equal_to(
        b"captured\n",
    )
    assert_that(_object_exists(repo, _BEYOND_LINK)).is_false()


@_DIR_FD_ONLY
@pytest.mark.parametrize("operation", ["write", "delete"])
@pytest.mark.parametrize("escape", ["outside", "inside"])
def test_restore_never_follows_parent_swapped_after_plan(
    byte_repo: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    escape: str,
) -> None:
    """A parent swapped between the restore plan and its writes stops the rollback."""
    repo = byte_repo
    (repo / "sub").mkdir()
    name = "f.txt" if operation == "write" else "created.txt"
    if operation == "write":
        (repo / "sub/f.txt").write_bytes(b"captured\n")
    checkpoint = capture_checkpoint([f"sub/{name}"], workspace_root=repo)
    assert checkpoint is not None
    (repo / "sub" / name).write_bytes(b"edited after capture\n")
    destination = _linked_destination(repo, tmp_path_factory, escape)
    (destination / name).write_bytes(_BEYOND_LINK)
    outside_before = {
        path.name: path.read_bytes() for path in sorted(destination.iterdir())
    }
    before = _user_state(repo)
    real_blob_for_path = checkpoints._blob_for_path

    def plan_then_swap(**kwargs: Any) -> Any:
        blob = real_blob_for_path(**kwargs)
        if not (repo / "sub").is_symlink():
            _replace_with_symlink(repo / "sub", destination)
        return blob

    monkeypatch.setattr(checkpoints, "_blob_for_path", plan_then_swap)

    with pytest.raises(CheckpointError, match="symbolic link"):
        restore_checkpoint(checkpoint)

    assert_that((repo / "sub").is_symlink()).is_true()
    outside_after = {
        path.name: path.read_bytes() for path in sorted(destination.iterdir())
    }
    # Nothing written, deleted or left behind as a temporary file.
    assert_that(outside_after).is_equal_to(outside_before)
    assert_that(_user_state(repo)).is_equal_to(before)


@pytest.mark.parametrize("blocked", ["zz/f.txt", "zz/deep/f.txt"])
@pytest.mark.parametrize("dir_fd_access", [True, False])
def test_restore_reports_a_file_ancestor_as_a_checkpoint_error(
    byte_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    blocked: str,
    dir_fd_access: bool,
) -> None:
    """A regular file where a restore must create a directory is a CheckpointError."""
    if dir_fd_access and not checkpoints._DIR_FD_ACCESS:
        pytest.skip("descriptor-relative access needs dir_fd support")
    monkeypatch.setattr(checkpoints, "_DIR_FD_ACCESS", dir_fd_access)
    repo = byte_repo
    (repo / blocked).parent.mkdir(parents=True)
    (repo / blocked).write_bytes(b"captured\n")
    checkpoint = capture_checkpoint(["target.txt", blocked], workspace_root=repo)
    assert checkpoint is not None
    (repo / "target.txt").write_bytes(b"edited after capture\n")
    shutil.rmtree(repo / "zz")
    (repo / "zz").write_bytes(b"a file now\n")

    with pytest.raises(CheckpointError) as raised:
        restore_checkpoint(checkpoint)

    # Both access paths name the repo-relative target, never the absolute one.
    assert_that(str(raised.value)).is_equal_to(
        f"Cannot restore checkpoint target, an ancestor is not a directory: "
        f"{blocked}",
    )
    # Per-target, not transactional: the earlier target is already restored.
    assert_that((repo / "target.txt").read_bytes()).is_equal_to(b"committed\n")
    assert_that((repo / "zz").read_bytes()).is_equal_to(b"a file now\n")
    assert_that([str(path) for path in repo.rglob("*.lintro-tmp")]).is_empty()


@pytest.mark.parametrize("created", ["zz/created.txt", "zz/deep/created.txt"])
@pytest.mark.parametrize("dir_fd_access", [True, False])
def test_restore_skips_deleting_below_a_file_ancestor(
    byte_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    created: str,
    dir_fd_access: bool,
) -> None:
    """A target to delete below a regular-file ancestor has nothing to delete."""
    if dir_fd_access and not checkpoints._DIR_FD_ACCESS:
        pytest.skip("descriptor-relative access needs dir_fd support")
    monkeypatch.setattr(checkpoints, "_DIR_FD_ACCESS", dir_fd_access)
    repo = byte_repo
    # Absent at capture, so the rollback plans a deletion for it.
    checkpoint = capture_checkpoint(["target.txt", created], workspace_root=repo)
    assert checkpoint is not None
    (repo / "target.txt").write_bytes(b"edited after capture\n")
    (repo / "zz").write_bytes(b"a file now\n")

    restore_checkpoint(checkpoint)

    assert_that((repo / "target.txt").read_bytes()).is_equal_to(b"committed\n")
    assert_that((repo / "zz").read_bytes()).is_equal_to(b"a file now\n")
    assert_that([str(path) for path in repo.rglob("*.lintro-tmp")]).is_empty()


def test_fallback_restore_deletes_a_target_in_a_normal_directory(
    byte_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The by-path fallback really deletes a target created after capture.

    Pins the narrowed suppression in ``_restore_by_path``: only a missing
    target or a non-directory ancestor is skipped, never an ordinary delete.
    """
    monkeypatch.setattr(checkpoints, "_DIR_FD_ACCESS", False)
    repo = byte_repo
    (repo / "sub").mkdir()
    (repo / "sub/kept.txt").write_bytes(b"untracked sibling\n")
    # Absent at capture, so the rollback plans a deletion for it.
    checkpoint = capture_checkpoint(
        ["target.txt", "sub/created.txt"],
        workspace_root=repo,
    )
    assert checkpoint is not None
    (repo / "target.txt").write_bytes(b"edited after capture\n")
    (repo / "sub/created.txt").write_bytes(b"created after capture\n")

    restore_checkpoint(checkpoint)

    assert_that((repo / "sub/created.txt").exists()).is_false()
    assert_that((repo / "sub/created.txt").is_symlink()).is_false()
    # Only the recorded target goes; its directory and sibling stay.
    assert_that((repo / "sub").is_dir()).is_true()
    assert_that((repo / "sub/kept.txt").read_bytes()).is_equal_to(
        b"untracked sibling\n",
    )
    assert_that((repo / "target.txt").read_bytes()).is_equal_to(b"committed\n")


_POSIX_SYMLINKS = pytest.mark.skipif(
    sys.platform == "win32",
    reason="needs POSIX symbolic link semantics",
)


def _link_parent_after_plan(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    destination: Path,
) -> Checkpoint:
    """Plan deleting ``sub/created.txt``, then swap ``sub`` for a link.

    The swap lands after the pre-write ``_beyond_symlink`` check, so only the
    write phase sees the link, as in a concurrent swap mid-restore.

    Args:
        repo: Test repository root.
        monkeypatch: Patches the plan step to perform the swap.
        destination: Path the swapped-in ``sub`` link points at.

    Returns:
        The checkpoint whose rollback plans the deletion.
    """
    (repo / "sub").mkdir()
    # Absent at capture, so the rollback plans a deletion for it.
    checkpoint = capture_checkpoint(
        ["target.txt", "sub/created.txt"],
        workspace_root=repo,
    )
    assert checkpoint is not None
    (repo / "target.txt").write_bytes(b"edited after capture\n")
    (repo / "sub/created.txt").write_bytes(b"created after capture\n")
    real_blob_for_path = checkpoints._blob_for_path

    def plan_then_link(**kwargs: Any) -> Any:
        blob = real_blob_for_path(**kwargs)
        if not (repo / "sub").is_symlink():
            (repo / "sub/created.txt").unlink()
            (repo / "sub").rmdir()
            (repo / "sub").symlink_to(destination)
        return blob

    monkeypatch.setattr(checkpoints, "_blob_for_path", plan_then_link)
    return checkpoint


def _non_directory_destination(
    tmp_path_factory: pytest.TempPathFactory,
    link: str,
) -> Path:
    """Return a regular file outside the repository, or a missing path.

    Args:
        tmp_path_factory: Source of directories outside the repository.
        link: ``file`` for a link to a regular file, ``dangling`` otherwise.

    Returns:
        The path the swapped-in link will point at.
    """
    outside = tmp_path_factory.mktemp("link-destination")
    if link == "dangling":
        return outside / "missing"
    (outside / "file.txt").write_bytes(_BEYOND_LINK)
    return outside / "file.txt"


@_POSIX_SYMLINKS
@pytest.mark.parametrize("link", ["file", "dangling"])
def test_fallback_restore_deletes_nothing_below_a_swapped_in_link(
    byte_repo: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    link: str,
) -> None:
    """The by-path fallback skips a deletion below a link it cannot see.

    Pins the divergence documented on ``_restore_by_path``: an ancestor
    swapped mid-restore for a link to a file or a dangling link makes the
    deletion a silent no-op, and the rollback goes on. On POSIX those surface
    as ENOTDIR and ENOENT; only the outcome is asserted.
    """
    monkeypatch.setattr(checkpoints, "_DIR_FD_ACCESS", False)
    repo = byte_repo
    destination = _non_directory_destination(tmp_path_factory, link)
    checkpoint = _link_parent_after_plan(repo, monkeypatch, destination)

    restore_checkpoint(checkpoint)

    assert_that((repo / "sub").is_symlink()).is_true()
    assert_that(os.readlink(repo / "sub")).is_equal_to(str(destination))
    if link == "file":
        assert_that(destination.read_bytes()).is_equal_to(_BEYOND_LINK)
    else:
        assert_that(os.path.lexists(destination)).is_false()
    # No error: the remaining targets are still rolled back.
    assert_that((repo / "target.txt").read_bytes()).is_equal_to(b"committed\n")
    assert_that([str(path) for path in repo.rglob("*.lintro-tmp")]).is_empty()


@_POSIX_SYMLINKS
@pytest.mark.parametrize("link", ["file", "dangling"])
def test_descriptor_restore_refuses_a_deletion_below_a_swapped_in_link(
    byte_repo: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    link: str,
) -> None:
    """The descriptor path reports the same swapped-in link as an error."""
    if not checkpoints._DIR_FD_ACCESS:
        pytest.skip("descriptor-relative access needs dir_fd support")
    repo = byte_repo
    destination = _non_directory_destination(tmp_path_factory, link)
    checkpoint = _link_parent_after_plan(repo, monkeypatch, destination)
    before = _user_state(repo)

    with pytest.raises(CheckpointError) as raised:
        restore_checkpoint(checkpoint)

    assert_that(str(raised.value)).is_equal_to(
        "Refusing to restore checkpoint target beyond a symbolic link: "
        "sub/created.txt",
    )
    assert_that((repo / "sub").is_symlink()).is_true()
    assert_that(os.readlink(repo / "sub")).is_equal_to(str(destination))
    if link == "file":
        assert_that(destination.read_bytes()).is_equal_to(_BEYOND_LINK)
    else:
        assert_that(os.path.lexists(destination)).is_false()
    assert_that([str(path) for path in repo.rglob("*.lintro-tmp")]).is_empty()
    assert_that(_user_state(repo)).is_equal_to(before)

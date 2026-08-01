"""Git-checkpoint snapshots for AI fix (and optional fmt) rollback.

Captures working-tree file state to ``refs/lintro/checkpoints/<run-id>`` using
git plumbing on a temporary index (``GIT_INDEX_FILE``). Never touches the
user's index, stash, or ``HEAD``.

Outside a usable git work tree (no git, bare repo, etc.) callers should fall
back to :mod:`lintro.ai.undo` file-content snapshots.
"""

from __future__ import annotations

import os
import shutil
import subprocess  # nosec B404 - subprocess invokes git with shell=False; args are plumbing only
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from lintro.ai.paths import atomic_write_bytes

CHECKPOINT_REF_PREFIX = "refs/lintro/checkpoints/"
DEFAULT_CHECKPOINT_RETENTION = 10
_GIT_TIMEOUT_SECONDS = 60.0
# Keep each ``git add`` argv comfortably under platform ARG_MAX limits.
_GIT_ADD_CHUNK = 500
# Git tree entry modes that are not plain file content.
_SYMLINK_MODE = "120000"
_GITLINK_MODE = "160000"
# Ambient git state that must never leak into these plumbing calls.
_INHERITED_GIT_VARS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_NAMESPACE",
)


@dataclass(frozen=True)
class Checkpoint:
    """A lintro-managed git tree snapshot.

    Attributes:
        ref: Full ref name under ``refs/lintro/checkpoints/``.
        run_id: Unique run identifier embedded in the ref.
        root: Absolute repository root path.
        paths: Repo-relative paths included in the snapshot.
        tree_sha: Object name of the written tree.
    """

    ref: str
    run_id: str
    root: Path
    paths: tuple[str, ...] = field(default_factory=tuple)
    tree_sha: str = ""


class CheckpointError(Exception):
    """Raised when a checkpoint capture, restore, or prune operation fails."""


def _git_bin() -> str | None:
    """Return the resolved git binary path, or None if unavailable."""
    return shutil.which("git")


def _git_env(overlay: dict[str, str] | None) -> dict[str, str]:
    """Build a hardened environment for a git plumbing call.

    Ambient ``GIT_*`` variables are stripped first. When lintro runs inside a
    git hook, git exports ``GIT_DIR``, ``GIT_INDEX_FILE`` and friends; a
    plumbing call that inherited them would target the hook's repository and
    index instead of the one at ``cwd``, breaking this module's promise never
    to touch the user's index.

    Args:
        overlay: Variables to set after sanitising (e.g. the temp index).

    Returns:
        The environment to pass to :func:`subprocess.run`.
    """
    full_env = os.environ.copy()
    for leaked in _INHERITED_GIT_VARS:
        full_env.pop(leaked, None)
    if overlay:
        full_env.update(overlay)
    # Avoid locale/pager interference and never open an editor.
    full_env.setdefault("GIT_TERMINAL_PROMPT", "0")
    full_env.setdefault("GIT_OPTIONAL_LOCKS", "0")
    # Target paths are literal filenames; ``*``, ``?``, ``[`` and a leading
    # ``:`` in a filename must not be read as pathspec magic.
    full_env["GIT_LITERAL_PATHSPECS"] = "1"
    return full_env


def _run_git(
    args: list[str],
    *,
    cwd: str,
    env: dict[str, str] | None = None,
    check: bool = False,
    binary: bool = False,
) -> subprocess.CompletedProcess[Any]:
    """Run a git command with optional temp-index environment.

    Args:
        args: Git arguments (without the leading ``git``).
        cwd: Working directory.
        env: Optional environment overlay (e.g. ``GIT_INDEX_FILE``).
        check: When True, raise :class:`CheckpointError` on non-zero exit.
        binary: When True, return raw ``bytes`` streams instead of decoded
            text. Required for blob contents, which are not always UTF-8.

    Returns:
        Completed process whose stdout/stderr are ``str`` (or ``bytes`` when
        ``binary`` is set).

    Raises:
        CheckpointError: When git is missing, times out, or ``check`` fails.
    """
    git_bin = _git_bin()
    if git_bin is None:
        raise CheckpointError("git is not installed or not on PATH")
    try:
        result = subprocess.run(  # nosec B603 - argv is [resolved git binary, *args]; shell=False; plumbing args only
            [git_bin, *args],
            cwd=cwd,
            capture_output=True,
            text=not binary,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
            env=_git_env(env),
        )
    except subprocess.TimeoutExpired as exc:
        raise CheckpointError(
            f"git {' '.join(args)} timed out after {_GIT_TIMEOUT_SECONDS}s",
        ) from exc
    except OSError as exc:
        raise CheckpointError(f"Failed to run git {' '.join(args)}: {exc}") from exc
    if check and result.returncode != 0:
        streams = [result.stderr, result.stdout]
        if binary:
            streams = [s.decode("utf-8", errors="replace") for s in streams]
        stderr = streams[0].strip() or streams[1].strip() or "unknown git error"
        raise CheckpointError(f"git {' '.join(args)} failed: {stderr}")
    return result


def git_checkpoints_available(workspace_root: Path) -> bool:
    """Return whether git checkpoints can be used under ``workspace_root``.

    Requires git on ``PATH``, a non-bare work tree, and a readable repo root.

    Args:
        workspace_root: Project directory to probe.

    Returns:
        True when capture/restore via git refs is supported.
    """
    if _git_bin() is None:
        return False
    root = str(workspace_root)
    try:
        inside = _run_git(
            ["rev-parse", "--is-inside-work-tree"],
            cwd=root,
        )
        if inside.returncode != 0 or inside.stdout.strip() != "true":
            return False
        bare = _run_git(
            ["rev-parse", "--is-bare-repository"],
            cwd=root,
        )
        if bare.returncode == 0 and bare.stdout.strip() == "true":
            return False
    except CheckpointError:
        return False
    return True


def _repo_root(workspace_root: Path) -> Path | None:
    """Resolve the git top-level directory for ``workspace_root``."""
    try:
        result = _run_git(
            ["rev-parse", "--show-toplevel"],
            cwd=str(workspace_root),
        )
    except CheckpointError:
        return None
    if result.returncode != 0:
        return None
    top = result.stdout.strip()
    return Path(top) if top else None


def _expand_directory(*, root: Path, rel_dir: str) -> list[str]:
    """List candidate files under ``rel_dir`` using git's own path filters.

    ``git ls-files -co --exclude-standard`` returns tracked plus untracked
    files while honouring ``.gitignore``. Walking the directory manually
    instead would drag ``.git/``, ``.venv/`` and every other ignored tree into
    the snapshot.

    Args:
        root: Repository root.
        rel_dir: Repo-relative directory (``""`` for the root itself).

    Returns:
        Repo-relative POSIX file paths, or an empty list when git fails.
    """
    args = ["ls-files", "-co", "--exclude-standard", "-z", "--"]
    args.append(rel_dir or ".")
    try:
        result = _run_git(args, cwd=str(root))
    except CheckpointError:
        return []
    if result.returncode != 0:
        return []
    return [entry for entry in result.stdout.split("\0") if entry]


def _normalize_paths(
    paths: list[str] | tuple[str, ...],
    *,
    root: Path,
) -> list[str]:
    """Convert paths to unique repo-relative POSIX strings.

    Directories are expanded through :func:`_expand_directory` so ignored
    trees never enter a snapshot. Files that do not exist are kept so that a
    restore can delete paths lintro created after capture.

    Args:
        paths: Absolute or relative path strings (files or directories).
        root: Repository root.

    Returns:
        Sorted unique repo-relative POSIX paths.
    """
    root_resolved = root.resolve()
    rels: set[str] = set()
    for raw in paths:
        if not raw:
            continue
        candidate = Path(raw)
        raw_abs = candidate if candidate.is_absolute() else root_resolved / candidate
        raw_abs = Path(os.path.normpath(raw_abs))
        # Resolve the parent, never the leaf: resolving the whole path would
        # rewrite a symlinked target to whatever it points at, and the
        # checkpoint would then snapshot (and restore) the wrong file.
        abs_path = raw_abs.parent.resolve() / raw_abs.name
        try:
            rel = abs_path.relative_to(root_resolved)
        except ValueError:
            logger.debug("Skipping path outside repo for checkpoint: {}", raw)
            continue
        if abs_path.is_dir() and not abs_path.is_symlink():
            rels.update(_expand_directory(root=root_resolved, rel_dir=rel.as_posix()))
        else:
            # Include missing paths so restore can delete files created later;
            # capture only adds existing files to the temp index.
            rels.add(rel.as_posix())
    return sorted(rels)


def _new_run_id() -> str:
    """Return a sortable unique run id (timestamp + short uuid)."""
    return f"{int(time.time())}-{uuid.uuid4().hex[:8]}"


def capture_checkpoint(
    paths: list[str] | tuple[str, ...],
    *,
    workspace_root: Path,
    run_id: str | None = None,
    keep: int = DEFAULT_CHECKPOINT_RETENTION,
) -> Checkpoint | None:
    """Snapshot target file state to ``refs/lintro/checkpoints/<run-id>``.

    Uses a temporary ``GIT_INDEX_FILE``: ``read-tree HEAD`` (or empty tree),
    ``add`` target paths (including untracked), ``write-tree``, ``update-ref``.
    Does not modify the user index, stash, or ``HEAD``.

    Args:
        paths: Files (and/or directories) to include in the snapshot.
        workspace_root: Directory inside the repository.
        run_id: Optional run identifier; generated when omitted.
        keep: Total checkpoint refs to retain, this run's included (default
            10). Older refs are pruned *before* the new one is written, so
            the checkpoint a run depends on is never the one pruned — even at
            ``keep=0``, which keeps only the current run's ref.

    Returns:
        The created :class:`Checkpoint`, or None when git checkpoints are
        unavailable or no paths remain after normalization.
    """
    if not git_checkpoints_available(workspace_root):
        return None
    root = _repo_root(workspace_root)
    if root is None:
        return None
    rel_paths = _normalize_paths(paths, root=root)
    if not rel_paths:
        return None

    rid = run_id or _new_run_id()
    ref = f"{CHECKPOINT_REF_PREFIX}{rid}"
    cwd = str(root)

    # Prune first: pruning after the write could delete the ref this very run
    # is about to hand back to the caller.
    try:
        prune_checkpoints(workspace_root=root, keep=max(keep - 1, 0))
    except CheckpointError as exc:
        logger.debug("Checkpoint prune skipped: {}", exc)

    fd, index_path = tempfile.mkstemp(prefix="lintro-git-index-")
    os.close(fd)
    index_file = Path(index_path)
    env = {"GIT_INDEX_FILE": str(index_file)}
    try:
        # Start from HEAD when available; otherwise empty tree (unborn branch).
        head = _run_git(["rev-parse", "--verify", "HEAD"], cwd=cwd)
        if head.returncode == 0:
            _run_git(["read-tree", "HEAD"], cwd=cwd, env=env, check=True)
        else:
            _run_git(["read-tree", "--empty"], cwd=cwd, env=env, check=True)

        # A target tracked in HEAD but already gone from the working tree
        # must be recorded as absent, or a rollback would resurrect it.
        missing = [p for p in rel_paths if not (root / p).exists()]
        for start in range(0, len(missing), _GIT_ADD_CHUNK):
            _run_git(
                [
                    "update-index",
                    "--force-remove",
                    "--",
                    *missing[start : start + _GIT_ADD_CHUNK],
                ],
                cwd=cwd,
                env=env,
                check=True,
            )

        existing = [p for p in rel_paths if (root / p).is_file()]
        # ``git add --`` updates only the temp index (via GIT_INDEX_FILE).
        # Chunked so a large target set cannot overflow the platform argv limit.
        for start in range(0, len(existing), _GIT_ADD_CHUNK):
            _run_git(
                ["add", "-f", "--", *existing[start : start + _GIT_ADD_CHUNK]],
                cwd=cwd,
                env=env,
                check=True,
            )

        tree = _run_git(["write-tree"], cwd=cwd, env=env, check=True)
        tree_sha = tree.stdout.strip()
        if not tree_sha:
            logger.debug("write-tree returned empty tree sha")
            return None

        _run_git(
            ["update-ref", ref, tree_sha],
            cwd=cwd,
            check=True,
        )
        logger.debug("Captured AI checkpoint {} ({})", ref, tree_sha)
    except CheckpointError:
        logger.debug("Checkpoint capture failed; caller should use file fallback")
        return None
    finally:
        index_file.unlink(missing_ok=True)

    return Checkpoint(
        ref=ref,
        run_id=rid,
        root=root,
        paths=tuple(rel_paths),
        tree_sha=tree_sha,
    )


def _blob_for_path(
    *,
    root: Path,
    treeish: str,
    rel_path: str,
) -> tuple[bytes, str] | None:
    """Return blob bytes and the git mode for ``rel_path`` at ``treeish``.

    Args:
        root: Repository root.
        treeish: Tree object (or ref) to read from.
        rel_path: Repo-relative path inside that tree.

    Returns:
        ``(contents, git mode)``, or None when the path is absent from the
        tree or is a submodule pointer, which cannot be restored from a blob.
        A tree entry that git then refuses to read raises
        :class:`CheckpointError` from :func:`_run_git`.
    """
    entry = _run_git(
        ["ls-tree", "-z", "--", treeish, rel_path],
        cwd=str(root),
    )
    if entry.returncode != 0 or not entry.stdout.strip():
        return None
    git_mode = entry.stdout.split(" ", 1)[0]
    if git_mode == _GITLINK_MODE:
        logger.debug("Skipping submodule entry in checkpoint: {}", rel_path)
        return None
    raw = _run_git(
        ["cat-file", "-p", f"{treeish}:{rel_path}"],
        cwd=str(root),
        check=True,
        binary=True,
    )
    return raw.stdout, git_mode


def restore_checkpoint(
    checkpoint: Checkpoint,
    paths: list[str] | tuple[str, ...] | None = None,
) -> None:
    """Restore files from a checkpoint tree.

    Every target blob is read before anything is written, so a tree that
    cannot be read fails the whole rollback without having touched the working
    tree. Each individual file is then replaced atomically and with its
    recorded mode; the write phase itself is sequential, so a failure part way
    through leaves the earlier files already restored. Paths present on disk
    but absent from the checkpoint tree are removed (they were created after
    capture). Paths that remain at the checkpoint content are rewritten from
    the tree — including when the user edited them between capture and
    rollback (lintro targets always return to the pre-batch snapshot).

    Only paths recorded on the checkpoint are eligible. Anything else is
    ignored rather than deleted, so a caller passing an unrelated (or
    unresolvable) path can never destroy a file that was never snapshotted.

    Args:
        checkpoint: Checkpoint produced by :func:`capture_checkpoint`.
        paths: Optional subset of paths to restore; defaults to all paths
            recorded on the checkpoint. A failed write propagates to the
            caller after the partial temporary file is cleaned up.
    """
    root = checkpoint.root
    treeish = checkpoint.tree_sha or checkpoint.ref
    known = set(checkpoint.paths)
    if paths is None:
        target_rels = list(checkpoint.paths)
    else:
        requested = _normalize_paths(list(paths), root=root)
        target_rels = [rel for rel in requested if rel in known]
        skipped = sorted(set(requested) - known)
        if skipped:
            logger.debug(
                "Skipping restore for paths absent from checkpoint {}: {}",
                checkpoint.ref,
                skipped,
            )

    planned: list[tuple[Path, tuple[bytes, str] | None]] = []
    for rel in target_rels:
        planned.append(
            (root / rel, _blob_for_path(root=root, treeish=treeish, rel_path=rel)),
        )

    # Read phase complete — apply all writes/deletes.
    for abs_path, blob in planned:
        if blob is None:
            # ``missing_ok`` because a concurrent delete between the read and
            # write phases must not abandon the rest of the rollback.
            abs_path.unlink(missing_ok=True)
            continue
        contents, git_mode = blob
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        if git_mode == _SYMLINK_MODE:
            # The blob of a symlink entry is its target path, not file content.
            abs_path.unlink(missing_ok=True)
            abs_path.symlink_to(contents.decode("utf-8", errors="replace"))
            continue
        if abs_path.is_symlink():
            # Never write through a link: that would rewrite its target.
            abs_path.unlink()
        atomic_write_bytes(
            abs_path,
            contents,
            fallback_mode=0o755 if git_mode.endswith("755") else 0o644,
        )


def diff_checkpoint(
    checkpoint: Checkpoint,
    paths: list[str] | tuple[str, ...] | None = None,
) -> str:
    """Return a unified diff of working tree vs the checkpoint for ``paths``.

    This is the accurate post-run "what lintro changed" report for files that
    were included in the checkpoint.

    The diff runs against a throwaway index loaded from the checkpoint tree.
    A plain ``git diff <tree>`` would consult the *real* index for the
    working-tree side and so report every untracked target as a deletion,
    which is precisely backwards for the files this feature exists to protect.

    Args:
        checkpoint: Checkpoint to diff against.
        paths: Optional path subset; defaults to checkpoint paths.

    Returns:
        Unified diff text (may be empty when nothing changed).
    """
    root = checkpoint.root
    known = set(checkpoint.paths)
    if paths is None:
        rels = list(checkpoint.paths)
    else:
        rels = [rel for rel in _normalize_paths(list(paths), root=root) if rel in known]
    if not rels:
        return ""
    treeish = checkpoint.tree_sha or checkpoint.ref

    fd, index_path = tempfile.mkstemp(prefix="lintro-git-diff-index-")
    os.close(fd)
    index_file = Path(index_path)
    env = {"GIT_INDEX_FILE": str(index_file)}
    try:
        _run_git(["read-tree", treeish], cwd=str(root), env=env, check=True)
        # ``--no-color`` defends against a user's ``color.ui = always``.
        # ``--no-textconv`` matters as much as ``--no-ext-diff``: a repo's
        # ``diff.*.textconv`` would otherwise run an external program here.
        result = _run_git(
            [
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--no-color",
                "--",
                *rels,
            ],
            cwd=str(root),
            env=env,
            check=True,
            binary=True,
        )
    finally:
        index_file.unlink(missing_ok=True)
    # Decoded leniently: a UTF-16 file with a ``diff`` attribute would make
    # strict locale decoding raise outside this module's error contract.
    diff_text: str = result.stdout.decode("utf-8", errors="replace")
    return diff_text


def checkpoint_ref_exists(checkpoint: Checkpoint) -> bool:
    """Return whether a checkpoint's ref is still present.

    A later capture in the same run can prune an earlier ref under a tight
    retention setting, which would make a printed ``git diff <ref>`` hint fail
    even though the tree object itself is still reachable.

    Args:
        checkpoint: Checkpoint to probe.

    Returns:
        True when the ref still resolves.
    """
    try:
        result = _run_git(
            ["show-ref", "--verify", "--quiet", checkpoint.ref],
            cwd=str(checkpoint.root),
        )
    except CheckpointError:
        return False
    return result.returncode == 0


def list_checkpoint_refs(*, workspace_root: Path) -> list[str]:
    """List lintro checkpoint refs (oldest first).

    Args:
        workspace_root: Directory inside the repository.

    Returns:
        Full ref names sorted lexicographically (timestamp-prefixed run ids).
    """
    if not git_checkpoints_available(workspace_root):
        return []
    root = _repo_root(workspace_root)
    if root is None:
        return []
    result = _run_git(
        [
            "for-each-ref",
            "--format=%(refname)",
            "--sort=refname",
            CHECKPOINT_REF_PREFIX,
        ],
        cwd=str(root),
    )
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def prune_checkpoints(
    *,
    workspace_root: Path,
    keep: int = DEFAULT_CHECKPOINT_RETENTION,
) -> int:
    """Delete oldest checkpoint refs beyond ``keep``.

    Args:
        workspace_root: Directory inside the repository.
        keep: Number of newest refs to retain. When ``keep <= 0``, all
            checkpoint refs are deleted.

    Returns:
        Number of refs deleted.
    """
    refs = list_checkpoint_refs(workspace_root=workspace_root)
    if keep < 0:
        keep = 0
    if len(refs) <= keep:
        return 0
    to_delete = refs if keep == 0 else refs[: len(refs) - keep]
    root = _repo_root(workspace_root)
    if root is None:
        return 0
    deleted = 0
    for ref in to_delete:
        # Tolerate a ref another lintro process already removed; pruning is
        # best-effort housekeeping and must never fail a fix run.
        result = _run_git(["update-ref", "-d", ref], cwd=str(root))
        if result.returncode == 0:
            deleted += 1
        else:
            logger.debug("Could not prune checkpoint ref {}", ref)
    return deleted


__all__ = [
    "CHECKPOINT_REF_PREFIX",
    "DEFAULT_CHECKPOINT_RETENTION",
    "Checkpoint",
    "CheckpointError",
    "capture_checkpoint",
    "checkpoint_ref_exists",
    "diff_checkpoint",
    "git_checkpoints_available",
    "list_checkpoint_refs",
    "prune_checkpoints",
    "restore_checkpoint",
]

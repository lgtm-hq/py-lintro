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

from loguru import logger

CHECKPOINT_REF_PREFIX = "refs/lintro/checkpoints/"
DEFAULT_CHECKPOINT_RETENTION = 10
_GIT_TIMEOUT_SECONDS = 60.0


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


def _run_git(
    args: list[str],
    *,
    cwd: str,
    env: dict[str, str] | None = None,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run a git command with optional temp-index environment.

    Args:
        args: Git arguments (without the leading ``git``).
        cwd: Working directory.
        env: Optional environment overlay (e.g. ``GIT_INDEX_FILE``).
        check: When True, raise :class:`CheckpointError` on non-zero exit.

    Returns:
        Completed process with text stdout/stderr.

    Raises:
        CheckpointError: When git is missing, times out, or ``check`` fails.
    """
    git_bin = _git_bin()
    if git_bin is None:
        raise CheckpointError("git is not installed or not on PATH")
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    # Avoid locale/pager interference and never open an editor.
    full_env.setdefault("GIT_TERMINAL_PROMPT", "0")
    full_env.setdefault("GIT_OPTIONAL_LOCKS", "0")
    try:
        result = subprocess.run(  # nosec B603 - argv is [resolved git binary, *args]; shell=False; plumbing args only
            [git_bin, *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
            env=full_env,
        )
    except subprocess.TimeoutExpired as exc:
        raise CheckpointError(
            f"git {' '.join(args)} timed out after {_GIT_TIMEOUT_SECONDS}s",
        ) from exc
    except OSError as exc:
        raise CheckpointError(f"Failed to run git {' '.join(args)}: {exc}") from exc
    if check and result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip() or "unknown git error"
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


def _normalize_paths(
    paths: list[str] | tuple[str, ...],
    *,
    root: Path,
) -> list[str]:
    """Convert paths to unique repo-relative POSIX strings.

    Args:
        paths: Absolute or relative path strings (files or directories).
        root: Repository root.

    Returns:
        Sorted unique relative paths that exist on disk (files only expanded
        from directories via a shallow walk of existing files).
    """
    root_resolved = root.resolve()
    rels: set[str] = set()
    for raw in paths:
        if not raw:
            continue
        candidate = Path(raw)
        abs_path = (
            candidate.resolve()
            if candidate.is_absolute()
            else (root_resolved / candidate).resolve()
        )
        try:
            rel = abs_path.relative_to(root_resolved)
        except ValueError:
            logger.debug("Skipping path outside repo for checkpoint: {}", raw)
            continue
        if abs_path.is_dir():
            for file_path in abs_path.rglob("*"):
                if file_path.is_file():
                    try:
                        rels.add(
                            file_path.resolve().relative_to(root_resolved).as_posix(),
                        )
                    except ValueError:
                        continue
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
        keep: Maximum checkpoint refs to retain after capture (default 10).

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

        existing = [p for p in rel_paths if (root / p).is_file()]
        if existing:
            # ``git add --`` updates only the temp index (via GIT_INDEX_FILE).
            _run_git(
                ["add", "-f", "--", *existing],
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

    try:
        prune_checkpoints(workspace_root=root, keep=keep)
    except CheckpointError as exc:
        logger.debug("Checkpoint prune skipped: {}", exc)

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
) -> bytes | None:
    """Return blob bytes for ``rel_path`` at ``treeish``, or None if absent."""
    exists = _run_git(
        ["cat-file", "-e", f"{treeish}:{rel_path}"],
        cwd=str(root),
    )
    if exists.returncode != 0:
        return None
    git_bin = _git_bin()
    if git_bin is None:
        raise CheckpointError("git is not installed or not on PATH")
    # Binary-safe read (avoid text-mode decoding).
    raw = subprocess.run(  # nosec B603 - argv is [git, cat-file, -p, tree:path]; shell=False
        [git_bin, "cat-file", "-p", f"{treeish}:{rel_path}"],
        cwd=str(root),
        capture_output=True,
        timeout=_GIT_TIMEOUT_SECONDS,
        check=False,
    )
    if raw.returncode != 0:
        raise CheckpointError(
            f"cat-file -p failed for {rel_path}: "
            f"{raw.stderr.decode('utf-8', errors='replace')}",
        )
    return raw.stdout


def restore_checkpoint(
    checkpoint: Checkpoint,
    paths: list[str] | tuple[str, ...] | None = None,
) -> None:
    """Restore files from a checkpoint tree (atomic across the batch).

    Reads all target blobs first, then writes them with atomic replace so a
    mid-batch failure does not leave a half-restored set. Paths present on
    disk but absent from the checkpoint tree are removed (they were created
    after capture). Paths that remain at the checkpoint content are rewritten
    from the tree — including when the user edited them between capture and
    rollback (lintro targets always return to the pre-batch snapshot).

    Args:
        checkpoint: Checkpoint produced by :func:`capture_checkpoint`.
        paths: Optional subset of paths to restore; defaults to all paths
            recorded on the checkpoint.

    Raises:
        BaseException: Re-raised after cleanup when an atomic write fails.
    """
    root = checkpoint.root
    treeish = checkpoint.tree_sha or checkpoint.ref
    if paths is None:
        target_rels = list(checkpoint.paths)
    else:
        target_rels = _normalize_paths(list(paths), root=root)
        # Also accept already-relative paths that were in the checkpoint list.
        for raw in paths:
            if raw and raw not in target_rels:
                target_rels.append(Path(raw).as_posix())

    planned: list[tuple[Path, bytes | None]] = []
    for rel in target_rels:
        abs_path = root / rel
        blob = _blob_for_path(root=root, treeish=treeish, rel_path=rel)
        planned.append((abs_path, blob))

    # Phase 1 complete — apply all writes/deletes.
    for abs_path, blob in planned:
        if blob is None:
            if abs_path.is_file():
                abs_path.unlink()
            continue
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(abs_path.parent), suffix=".lintro-restore")
        try:
            try:
                fobj = os.fdopen(fd, "wb")
            except BaseException:
                os.close(fd)
                raise
            with fobj:
                fobj.write(blob)
            Path(tmp).replace(abs_path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise


def diff_checkpoint(
    checkpoint: Checkpoint,
    paths: list[str] | tuple[str, ...] | None = None,
) -> str:
    """Return a unified diff of working tree vs the checkpoint for ``paths``.

    This is the accurate post-run "what lintro changed" report for files that
    were included in the checkpoint.

    Args:
        checkpoint: Checkpoint to diff against.
        paths: Optional path subset; defaults to checkpoint paths.

    Returns:
        Unified diff text (may be empty when nothing changed).
    """
    root = checkpoint.root
    rels = (
        list(checkpoint.paths)
        if paths is None
        else _normalize_paths(list(paths), root=root)
    )
    if not rels:
        return ""
    treeish = checkpoint.tree_sha or checkpoint.ref
    # Compare checkpoint tree to the working tree for the given paths.
    result = _run_git(
        ["diff", "--no-ext-diff", treeish, "--", *rels],
        cwd=str(root),
        check=True,
    )
    return result.stdout


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
        _run_git(["update-ref", "-d", ref], cwd=str(root), check=True)
        deleted += 1
    return deleted


__all__ = [
    "CHECKPOINT_REF_PREFIX",
    "DEFAULT_CHECKPOINT_RETENTION",
    "Checkpoint",
    "CheckpointError",
    "capture_checkpoint",
    "diff_checkpoint",
    "git_checkpoints_available",
    "list_checkpoint_refs",
    "prune_checkpoints",
    "restore_checkpoint",
]

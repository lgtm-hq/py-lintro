"""A working tree at the pull request's head for the agent to read (#2733).

On ``--pr`` the diff and refs come from ``gh`` and the CLI transport's agent
(the question pass, every chunk call, the synthesis and verification passes)
used to explore whatever tree the command ran in: today's ``main`` for a
corpus replay of a merged PR, so questions and findings could cite code the
PR never saw (#2732). This module pins that tree to the PR head: it fetches
``refs/pull/<n>/head`` into a private ref and checks the head commit out in
a temporary worktree under the cache directory, which becomes ``repo_root``
for every provider call of the run and is removed when the run ends,
whichever way it ends. When no repository is available the run is told so
and every call goes out without tools (:attr:`ReviewCheckout.NONE`), never
against the ambient tree.

Ownership of the tree, from creation to removal, is split four ways:
:class:`~lintro.ai.review.pr_head_guard.RemoveOnError` covers preparation
(collection's filters and validation, then everything ``prepare_review`` does
after collection); ``PreparedReview.discard()`` covers the adapter window
between ``prepare_review`` and ``execute_review`` (a converged round, a
provider that fails to construct); the run's own ``finally`` in
``run_review_async`` covers execution; and the ``atexit`` registry here is the
backstop for whatever none of them reached. A SIGKILL escapes all four and is
swept by the next run.
"""

from __future__ import annotations

import atexit
import contextlib
import os
import shutil
import stat
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, TYPE_CHECKING

from loguru import logger

from lintro.ai.enums import AITransport
from lintro.ai.review.context.git_ops import _run_git
from lintro.ai.review.enums.coverage_degradation_reason import (
    CoverageDegradationReason,
)
from lintro.ai.review.exceptions import ReviewContextError
from lintro.ai.review.models.coverage_degradation import (
    CARRIED_CHUNK_INDEX,
    CoverageDegradation,
)

if TYPE_CHECKING:
    from lintro.ai.config import AIConfig
    from lintro.ai.review.run_planning import ReviewRunPlan

__all__ = [
    "PrHeadWorktree",
    "checkout_pr_head",
    "no_tree_degradations",
    "prune_stale_pr_worktrees",
    "remove_pr_head",
]

#: Where the head worktrees live, relative to the repository root.
_WORKTREE_DIR = Path("lintro") / "pr-heads"
_OID_PREFIX_LEN = 12


def _cache_dir(repo_root: str) -> Path | None:
    """Resolve the worktree cache directory, refusing a symlinked path.

    The cache lives under the git common directory (``.git/lintro/pr-heads``),
    which no tracked file can reach: a checkout cannot plant a symlink there
    the way it could under the working tree. The sweep removes whole
    directories under this path, so no component of it may be a symlink even
    so.

    Args:
        repo_root: The repository the cache belongs to.

    Returns:
        The cache directory, created when missing; ``None`` when the git
        directory cannot be resolved or any component is a symlink.
    """
    try:
        common = _run_git(
            args=["-C", repo_root, "rev-parse", "--git-common-dir"],
            check=False,
        )
        if common.returncode != 0 or not common.stdout.strip():
            return None
        git_dir = Path(common.stdout.strip())
        if not git_dir.is_absolute():
            git_dir = Path(repo_root) / git_dir
        current = git_dir
        for part in _WORKTREE_DIR.parts:
            current = current / part
            if current.is_symlink():
                logger.warning(
                    "{} is a symlink; refusing to use it for PR head worktrees.",
                    current,
                )
                return None
        current.mkdir(parents=True, exist_ok=True)
    except (ReviewContextError, OSError) as exc:
        # An unwritable or unreadable git directory is no reason to fail the
        # review: the run degrades to no tree like any other checkout failure.
        logger.warning("Cannot use the PR head cache ({}); no tree for the run.", exc)
        return None
    return current


def _is_owned_name(name: str) -> bool:
    """Tell whether a cache entry name is one :func:`checkout_pr_head` writes.

    Args:
        name: The directory name, expected as ``<pr>-<oid12>-<pid>``.

    Returns:
        True for a name of that shape; anything else is not ours to remove.
    """
    parts = name.split("-")
    if len(parts) != 3:
        return False
    number, oid, pid = parts
    return (
        number.isdigit()
        and len(oid) == _OID_PREFIX_LEN
        and all(char in "0123456789abcdef" for char in oid)
        and pid.isdigit()
    )


@dataclass(frozen=True, slots=True)
class PrHeadWorktree:
    """A checked-out PR head the run may read.

    The path is unique to the run (the PR, the head and this process), and
    the run holds an exclusive lock on ``<path>.lock`` for its whole life:
    another run's startup sweep removes a worktree only after winning that
    lock, so a live run's tree is never pulled from under it.

    Attributes:
        path: Absolute path of the worktree; the run's ``repo_root``.
        repo_root: The repository the worktree belongs to (for removal).
        head_oid: The commit checked out, verified against the PR's head.
        lock: The held lock file, released on removal.
    """

    path: str
    repo_root: str
    head_oid: str
    lock: IO[str] | None = field(default=None, compare=False, repr=False)


def _open_lock(worktree_path: Path) -> IO[str] | None:
    """Open the lock file beside a worktree, kept open for as long as it is held.

    The lock name is predictable (PR, head, pid), so a checkout could plant
    a symlink there and have the run write through it: the open never
    follows a symlink, and anything but a regular file is refused.

    Args:
        worktree_path: The worktree the lock guards.

    Returns:
        The open lock file (:func:`_release` closes it), or ``None`` when
        the path is a symlink or not a regular file.
    """
    lock_path = f"{worktree_path}.lock"
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        logger.warning("Cannot open {} as a lock file: {}", lock_path, exc)
        return None
    if not stat.S_ISREG(os.fstat(fd).st_mode) or Path(lock_path).is_symlink():
        os.close(fd)
        logger.warning("{} is not a regular file; refusing it as a lock.", lock_path)
        return None
    return os.fdopen(fd, "r+", encoding="utf-8")


def _try_lock(fh: IO[str]) -> bool:
    """Take an exclusive, non-blocking lock on ``fh``.

    Args:
        fh: An open lock file.

    Returns:
        True when the lock was taken; False when another process holds it.
    """
    try:
        if sys.platform == "win32":
            import msvcrt

            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _release(fh: IO[str] | None) -> None:
    """Release and close a lock file, tolerating one already closed.

    Args:
        fh: The lock file, or ``None``.
    """
    if fh is None or fh.closed:
        return
    with contextlib.suppress(OSError):
        if sys.platform == "win32":
            import msvcrt

            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    with contextlib.suppress(OSError):
        fh.close()


def checkout_pr_head(*, pr_number: int, head_oid: str) -> PrHeadWorktree | None:
    """Fetch the PR head and check it out in a temporary worktree.

    Args:
        pr_number: The pull request number, for ``refs/pull/<n>/head``.
        head_oid: The head commit ``gh`` reported; the fetched ref must
            resolve to it, or the worktree is not created.

    Returns:
        The worktree, or ``None`` when the command does not run inside a
        git repository, the fetch is refused, or the fetched head is not
        the one ``gh`` reported. ``None`` means "no tree": the caller runs
        without tools rather than against the ambient tree.
    """
    try:
        root = _run_git(args=["rev-parse", "--show-toplevel"], check=False)
    except ReviewContextError:
        return None
    if root.returncode != 0 or not root.stdout.strip():
        return None
    repo_root = root.stdout.strip()
    prune_stale_pr_worktrees(repo_root=repo_root)
    ref = f"refs/lintro/pr/{pr_number}"
    try:
        fetched = _run_git(
            args=["fetch", "--no-tags", "origin", f"+refs/pull/{pr_number}/head:{ref}"],
            check=False,
        )
        if fetched.returncode != 0:
            logger.warning(
                "Could not fetch refs/pull/{}/head; the review runs without a tree.",
                pr_number,
            )
            return None
        resolved = _run_git(args=["rev-parse", ref], check=False)
        if resolved.returncode != 0 or resolved.stdout.strip() != head_oid:
            logger.warning(
                "refs/pull/{}/head resolved to {} but gh reported {}; the review "
                "runs without a tree.",
                pr_number,
                resolved.stdout.strip()[:12],
                head_oid[:12],
            )
            return None
        base = _cache_dir(repo_root)
        if base is None:
            return None
        path = base / f"{pr_number}-{head_oid[:_OID_PREFIX_LEN]}-{os.getpid()}"
        lock = _open_lock(path)
        if lock is None:
            return None
        if not _try_lock(lock):
            # Only this process can hold a lock at this pid-unique path; a
            # held one is a prior incarnation still winding down. Do not race.
            lock.close()
            logger.warning(
                "PR head worktree {} is locked; the review runs without a tree.",
                path,
            )
            return None
        lock.seek(0)
        lock.truncate()
        lock.write(f"{os.getpid()}\n")
        lock.flush()
        added = _run_git(
            args=["worktree", "add", "--detach", str(path), head_oid],
            check=False,
        )
        if added.returncode != 0:
            logger.warning(
                "Could not check out the PR head in a worktree ({}); the review "
                "runs without a tree.",
                added.stderr.strip()[:200],
            )
            _release(lock)
            with contextlib.suppress(OSError):
                Path(f"{path}.lock").unlink(missing_ok=True)
            return None
    except ReviewContextError as exc:
        logger.warning(
            "PR head checkout failed ({}); the review runs without a tree.",
            exc,
        )
        return None
    logger.info(
        "Reviewing PR #{} against its head {} at {}",
        pr_number,
        head_oid[:12],
        path,
    )
    return _owned(
        PrHeadWorktree(
            path=str(path),
            repo_root=repo_root,
            head_oid=head_oid,
            lock=lock,
        ),
    )


_LIVE: dict[str, PrHeadWorktree] = {}


def _remove_live_at_exit() -> None:
    """Remove every tree still registered when the interpreter exits."""
    for worktree in list(_LIVE.values()):
        remove_pr_head(worktree)


atexit.register(_remove_live_at_exit)


def _owned(worktree: PrHeadWorktree) -> PrHeadWorktree:
    """Register a tree for removal at its creation.

    The run removes the tree itself when it ends, but the tree exists from
    context collection on, before the run is entered: a provider that fails
    to construct or a command that exits early ("already converged") would
    otherwise leave it until a later sweep. One module-level registry backs
    one ``atexit`` hook, and :func:`remove_pr_head` drops the entry, so a
    long-lived process (the MCP server) accumulates nothing across runs.

    Args:
        worktree: The tree just created.

    Returns:
        The same tree.
    """
    _LIVE[worktree.path] = worktree
    return worktree


def empty_workspace() -> PrHeadWorktree:
    """Create an empty directory for a run that has no tree to offer.

    Every CLI transport runs its agent in ``repo_root``, and only some can
    have their tools switched off, so a run without a tree confines the
    agent to an empty directory rather than whatever ``cwd`` happens to be.
    :func:`remove_pr_head` removes it like a head worktree.

    Returns:
        The workspace handle; ``repo_root`` and ``head_oid`` are empty.
    """
    path = tempfile.mkdtemp(prefix="lintro-review-no-tree-")
    # An empty *repository*: the Codex CLI refuses to run outside one
    # ("Not inside a trusted directory"), and an empty history offers the
    # agent nothing more than an empty directory did.
    with contextlib.suppress(ReviewContextError):
        _run_git(args=["-C", path, "init", "-q"], check=False)
    return _owned(PrHeadWorktree(path=path, repo_root="", head_oid=""))


def remove_pr_head(worktree: PrHeadWorktree | None) -> None:
    """Remove a head worktree or empty workspace; a no-op for ``None``.

    Safe to call more than once and on every exit path: a worktree already
    gone is not an error, and a failure to remove is logged, not raised,
    since the review's result is already in hand. A symlink at the path is
    never followed into.

    Args:
        worktree: The worktree to remove.
    """
    if worktree is None:
        return
    _LIVE.pop(worktree.path, None)
    if worktree.repo_root:
        try:
            _run_git(
                args=[
                    "-C",
                    worktree.repo_root,
                    "worktree",
                    "remove",
                    "--force",
                    worktree.path,
                ],
                check=False,
            )
        except ReviewContextError as exc:
            logger.debug("git worktree remove failed: {}", exc)
    if Path(worktree.path).is_symlink():
        logger.warning("{} is a symlink; not removing it.", worktree.path)
    else:
        shutil.rmtree(worktree.path, ignore_errors=True)
    _release(worktree.lock)
    with contextlib.suppress(OSError):
        Path(f"{worktree.path}.lock").unlink(missing_ok=True)


def prune_stale_pr_worktrees(*, repo_root: str) -> None:
    """Drop head worktrees a killed run left behind.

    A SIGKILL cannot run the cleanup, so every run starts by pruning what
    an earlier one could not remove: git's own record first, then any
    directory under the cache path whose lock nobody holds. A directory
    whose lock is held belongs to a live run and is left alone.

    Args:
        repo_root: The repository whose cache directory to sweep.
    """
    base = _cache_dir(repo_root)
    if base is None:
        return
    with contextlib.suppress(ReviewContextError):
        _run_git(args=["-C", repo_root, "worktree", "prune"], check=False)
    try:
        entries = list(base.iterdir())
    except OSError as exc:
        # A sweep that cannot read the cache is skipped, never a failure of
        # the checkout that asked for it.
        logger.warning("Cannot sweep the PR head cache ({}); skipping.", exc)
        return
    for stale in entries:
        # Only what this module wrote, and never through a symlink.
        if stale.is_symlink() or not stale.is_dir():
            continue
        if not _is_owned_name(stale.name):
            continue
        probe = _open_lock(stale)
        if probe is None:
            continue
        if not _try_lock(probe):
            probe.close()
            continue  # a live run owns it
        remove_pr_head(
            PrHeadWorktree(
                path=str(stale),
                repo_root=repo_root,
                head_oid="",
                lock=probe,
            ),
        )


def no_tree_degradations(
    *,
    plan: ReviewRunPlan,
    ai_config: AIConfig,
) -> tuple[CoverageDegradation, ...]:
    """Return the run-level row recording that the agent had no tree.

    Args:
        plan: The resolved run plan (``tools_disabled``).
        ai_config: Effective AI configuration; only the CLI transport has an
            agent that could have read a tree.

    Returns:
        One ``NO_TREE_FOR_AGENT`` narrative row when tools were off for the
        run on the CLI transport, else nothing.
    """
    if not plan.tools_disabled or ai_config.transport is not AITransport.CLI:
        return ()
    return (
        CoverageDegradation(
            reason=CoverageDegradationReason.NO_TREE_FOR_AGENT,
            chunk_index=CARRIED_CHUNK_INDEX,
            split=False,
        ),
    )

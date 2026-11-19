"""Remove a cold npx install that did not finish, where it was started (#3530).

A cold run installs under lintro's exclusive per-pin lock. When npx stops
before it finishes (a timeout, an install error, an interrupted lintro), the
entry it was writing is removed, so the next run starts cold instead of
refusing a half-written entry.

That removal must only ever touch the cache lintro examined before the spawn.
The lock file lives beside the cache, not in it, so holding the lock does not
pin the cache directory: if the cache directory, or anything above it, is
renamed or replaced by a link during the run, the same path can name a
different directory afterwards. :func:`bound_cache` therefore records the
identity (device and inode) of the per-pin cache directory and its ``_npx``
before the spawn, creating them if npx has not yet, and keeps ``_npx`` open.
Cleanup refuses to remove anything unless the path still resolves to those
same two directories, and then renames and deletes relative to the open
``_npx`` descriptor, so no path above it is followed again.

On a platform without directory descriptors (Windows) the identities are
still compared before anything is removed, and the removal then goes by
path; a swap in the moment between that comparison and the rename is not
caught there.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import stat
from collections.abc import Collection, Iterator
from pathlib import Path

from loguru import logger

from lintro.utils.npx_aside import set_aside
from lintro.utils.npx_integrity import STALE_PREFIX, install_unfinished

#: Whether ``_npx`` can be held open and worked in by descriptor: the calls
#: :func:`~lintro.utils.npx_aside.set_aside` and :func:`install_unfinished`
#: make with ``dir_fd``. Python lists ``os.rename`` in ``supports_dir_fd``
#: but never ``os.replace``, though both take ``src_dir_fd``; the aside
#: helpers therefore call ``os.rename`` when given a descriptor.
_DIR_FD_OPS: bool = (
    {os.open, os.stat, os.rename, os.unlink, os.rmdir} <= os.supports_dir_fd
    and os.listdir in os.supports_fd
    and shutil.rmtree.avoids_symlink_attacks
    and hasattr(os, "O_DIRECTORY")
)

#: Flags ``_npx`` is opened with: a real directory, never through a link.
_DIR_FLAGS: int = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)

_Identity = tuple[int, int]


def _identity(info: os.stat_result) -> _Identity:
    """Return what identifies a directory on its filesystem.

    Args:
        info: The directory's stat result.

    Returns:
        Its device and inode.
    """
    return (info.st_dev, info.st_ino)


def _directory_identity(path: Path) -> _Identity:
    """Return the identity of the real directory at *path*, never via a link.

    Args:
        path: The directory.

    Returns:
        Its device and inode.

    Raises:
        NotADirectoryError: *path* is a link or not a directory.
    """
    info = os.stat(path, follow_symlinks=False)
    if not stat.S_ISDIR(info.st_mode):
        raise NotADirectoryError(f"{path} is not a real directory")
    return _identity(info)


class BoundCache:
    """A per-pin cache, as it was when lintro examined it before a cold spawn."""

    def __init__(self, cache: Path) -> None:
        """Create the cache and ``_npx`` if needed, and record which they are.

        Args:
            cache: The ``--cache`` the npx command carries.

        Raises:
            OSError: They cannot be created, opened or examined, or changed
                while they were being recorded.
        """
        self._cache = cache
        self._real = Path(os.path.realpath(cache))
        (self._real / "_npx").mkdir(parents=True, exist_ok=True)
        self._fd: int | None = None
        if _DIR_FD_OPS:
            self._fd = os.open(self._real / "_npx", _DIR_FLAGS)
        bound = False
        try:
            self._ids = self._path_identities()
            if self._fd is not None and _identity(os.fstat(self._fd)) != self._ids[1]:
                raise OSError(f"{self._real / '_npx'} changed while lintro opened it")
            bound = True
        finally:
            if not bound:
                self.close()

    def _path_identities(self) -> tuple[_Identity, _Identity]:
        """Return the identities the cache's path resolves to now.

        Returns:
            The cache directory's and ``_npx``'s.

        Raises:
            OSError: The path no longer resolves where it did, or either
                directory is missing or not a real directory.
        """
        if Path(os.path.realpath(self._cache)) != self._real:
            raise OSError(f"{self._cache} no longer resolves to {self._real}")
        return (
            _directory_identity(self._real),
            _directory_identity(self._real / "_npx"),
        )

    def close(self) -> None:
        """Let go of the open ``_npx``."""
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def discard_unfinished(
        self,
        keep: Collection[str],
        *,
        npx_stopped: bool,
    ) -> None:
        """Remove the entries npx left unfinished, if the cache is unchanged.

        Never raises: whatever stops the cleanup is logged, everything is
        left in place, and the next run verifies or refuses it. An exception
        from cleanup must not replace the one that ended the run.

        Args:
            keep: Names of the entries that were there before the spawn.
            npx_stopped: Whether lintro stopped npx before it exited by
                itself; then any fresh entry without a lockfile goes
                (:func:`~lintro.utils.npx_integrity.install_unfinished`).
        """
        try:
            unchanged = self._path_identities() == self._ids
            names = os.listdir(
                self._fd if self._fd is not None else self._real / "_npx",
            )
        except OSError as exc:
            self._leave(str(exc))
            return
        if not unchanged:
            self._leave(f"{self._real} or its _npx was replaced during the run")
            return
        for name in sorted(names):
            if name in keep or name.startswith(STALE_PREFIX):
                continue
            self._discard(name, npx_stopped=npx_stopped)

    def _leave(self, reason: str) -> None:
        """Log that nothing was removed, and why.

        Args:
            reason: Why the cleanup stopped.
        """
        logger.warning(
            f"lintro left npx's unfinished install in {self._cache} in place "
            f"({reason}); the next lintro run checks it",
        )

    def _discard(self, name: str, *, npx_stopped: bool) -> None:
        """Remove one entry if npx never finished installing it.

        Its npm ``concurrency.lock`` goes with it, so a run waiting for the
        pin, or the next one, starts cold at once.

        Args:
            name: The entry's name in ``_npx``.
            npx_stopped: Whether lintro stopped npx before it exited.
        """
        if self._fd is not None:
            entry, dir_fd = Path(name), self._fd
        else:
            entry, dir_fd = self._real / "_npx" / name, None
        if not install_unfinished(entry, dir_fd=dir_fd, npx_stopped=npx_stopped):
            return
        try:
            set_aside(entry, dir_fd=dir_fd)
        except OSError as exc:
            logger.warning(
                f"lintro could not remove npx's unfinished install "
                f"{self._real / '_npx' / name} ({exc}); the next lintro run "
                "removes it",
            )


@contextlib.contextmanager
def bound_cache(cache: Path) -> Iterator[BoundCache | None]:
    """Hold a cold cache's identity for the run, for cleanup afterwards.

    Args:
        cache: The ``--cache`` the npx command carries.

    Yields:
        BoundCache | None: The bound cache, or None when it could not be
        recorded; then nothing is removed after the run, and the next run
        checks what is left.
    """
    try:
        bound = BoundCache(cache)
    except OSError as exc:
        logger.warning(
            f"lintro cannot record its npm cache {cache} ({exc}); an install "
            "npx does not finish is left for the next run to check",
        )
        yield None
        return
    try:
        yield bound
    finally:
        bound.close()

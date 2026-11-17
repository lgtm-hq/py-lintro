"""Serialise the first npx install of each pin across lintro processes (#3421).

npx installs a cold pin and runs it in one process, and leaves the entry
unlocked at two points: between creating ``_npx/<hash>`` and taking npm's
``concurrency.lock`` on it, and while it rewrites the entry's ``package.json``
after the install. Another lintro run that reaches the entry then sees a half
install: its cache gate (:mod:`lintro.tools.core.npx_cache`) removes it from
under the installing npx, which fails with ``ENOENT``, and its spawn-time check
(:mod:`lintro.utils.npx_integrity`) refuses it. Two npx processes can also
both decide to install, and one rewrites ``node_modules`` while the other's
tool is loading it, npm's own race.

lintro therefore holds an OS file lock per pin, on
``<lintro cache>/npx/.<package>@<pin>.lintro-install.lock``, beside the per-pin
cache rather than inside it, so creating it never changes what the integrity
check fingerprints:

- **Before an npx fallback spawns**, the cache is checked under a shared lock.
  When it already holds an entry, the lock is released and npx runs at once,
  so warm runs never wait for one another.
- **When it holds none**, the run releases the shared lock, takes the lock
  exclusively, and checks again. Another run may have installed the pin
  meanwhile; it then runs warm. Otherwise it keeps the exclusive lock until
  npx has exited and its fresh install has been verified, so exactly one npx
  installs each pin at a time.
- **The cache gate** takes the shared lock without waiting. While a first
  install holds it exclusively, the gate leaves the cache alone and the
  spawn-time check, which waits, takes over.

Waiting counts against the tool's timeout. A wait that runs out raises
:class:`NpxInstallLockTimeoutError`, an :class:`NpxIntegrityError`, so the run is an
execution failure with incomplete findings, never a skip.

The lock is ``flock`` on POSIX and ``msvcrt.locking`` on Windows, which has no
shared mode, so there every holder is exclusive. The OS drops the lock when the
holder exits, so a crashed install never leaves a lock behind. The file itself
is never removed: a waiter and a newcomer would otherwise lock different files.
lintro takes this lock before npm's own lock, and never waits for it while
holding npm's, so the two cannot deadlock.

**A lock lintro cannot use fails the run.** The lock file is opened without
following a symbolic link and must be a regular file. If it cannot be
created, opened or locked (a directory or a link in its place, a directory
lintro cannot write, a filesystem without file locks), the run is an execution
failure naming the lock path, never a skipped or unavailable tool; the cache
gate leaves that refusal to the spawn-time check.

**A lock deleted or replaced while held.** A lock follows the file it was
taken on, not its name, so after taking it lintro checks that the file it
locked is still the one at the path, and otherwise opens the new file and
tries again within the same wait. A first install checks this once more just
before npx starts and fails the run if the lock was replaced. The limit: a
process of the same user that deletes the lock after that check, while npx is
installing, lets another run take a fresh lock and meet the half-written entry
again. lintro never deletes the lock file itself.
"""

from __future__ import annotations

import contextlib
import errno
import os
import stat
import sys
import time
from collections.abc import Iterator
from pathlib import Path

from loguru import logger

from lintro.utils.npx_integrity import NpxIntegrityError

#: Suffix of the per-pin lock file beside the per-pin cache.
INSTALL_LOCK_SUFFIX: str = ".lintro-install.lock"

#: Pause between attempts to take a lock another process holds.
_POLL_SECONDS: float = 0.05

#: Flags the lock file is opened with: never through a symbolic link, and
#: never blocking on a FIFO planted in its place.
_OPEN_FLAGS: int = (
    os.O_RDWR
    | os.O_CREAT
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_NONBLOCK", 0)
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_BINARY", 0)
)


#: Errors ``msvcrt.locking`` reports when another handle holds the region.
_WINDOWS_BUSY: frozenset[int] = frozenset(
    {errno.EACCES, errno.EDEADLK, getattr(errno, "EDEADLOCK", errno.EDEADLK)},
)


#: ``flock`` errors meaning the filesystem has no usable file locks.
_NO_FILE_LOCKS: frozenset[int] = frozenset(
    code
    for code in (
        getattr(errno, "ENOTSUP", None),
        getattr(errno, "EOPNOTSUPP", None),
        getattr(errno, "ENOLCK", None),
        getattr(errno, "ENOSYS", None),
    )
    if code is not None
)


class NpxInstallLockTimeoutError(NpxIntegrityError):
    """Another lintro run's first install of the pin outlasted the wait."""


class NpxInstallLockError(NpxIntegrityError):
    """lintro's install lock cannot be used, or was replaced while held."""


def install_lock_path(cache: Path) -> Path:
    """Return the lock file for one per-pin npm cache.

    Args:
        cache: The per-pin cache, ``<lintro cache>/npx/<package>@<pin>``.

    Returns:
        ``<lintro cache>/npx/.<package>@<pin>.lintro-install.lock``.
    """
    return cache.parent / f".{cache.name}{INSTALL_LOCK_SUFFIX}"


def _unusable(path: Path, reason: str) -> str:
    """Build the refusal for a lock lintro cannot use.

    Args:
        path: The lock file.
        reason: What is wrong with it.

    Returns:
        The message.
    """
    return (
        f"lintro cannot use its npx install lock {path}: {reason}. lintro did "
        "not run the tool, so its findings are incomplete. Make sure "
        f"{path} is a regular file (or absent) in a directory lintro can "
        "write, then run lintro again."
    )


def _no_file_locks(path: Path, exc: OSError) -> str:
    """Explain a filesystem that refuses file locks, with the remedy that works.

    lintro's cache root is fixed (``~/.lintro/cache``), so the remedy is a
    symbolic link from it to a local directory; lintro resolves that root
    before checking the per-pin caches against it.

    Args:
        path: The lock file.
        exc: The error ``flock`` raised.

    Returns:
        The message.
    """
    root = path.parent.parent
    return (
        f"the filesystem holding lintro's npx install lock {path} does not "
        f"support file locks ({_os_reason(exc)}), so lintro cannot stop two "
        "runs from installing the same pin at once. lintro did not run the "
        f"tool, so its findings are incomplete. Point lintro's cache {root} "
        "at a directory on a local filesystem with a symbolic link (move the "
        f"directory there and link {root} to it), then run lintro again."
    )


def _os_reason(exc: OSError) -> str:
    """Describe why the lock file could not be opened.

    Args:
        exc: The error from opening it.

    Returns:
        A short reason.
    """
    if exc.errno in {errno.ELOOP, getattr(errno, "EMLINK", -1)}:
        return "it is a symbolic link"
    if exc.errno == errno.EISDIR:
        return "it is a directory"
    return f"{exc.strerror or exc} (errno {exc.errno})"


def _open(path: Path) -> int:
    """Open, creating if needed, the regular lock file at *path*.

    Args:
        path: The lock file.

    Returns:
        The open descriptor. On Windows the file holds one byte, the region
        locked.

    Raises:
        NpxInstallLockError: The file cannot be created or opened, is a
            symbolic link, or is not a regular file.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not hasattr(os, "O_NOFOLLOW") and path.is_symlink():
            raise NpxInstallLockError(_unusable(path, "it is a symbolic link"))
        fd = os.open(path, _OPEN_FLAGS, 0o644)
    except NpxInstallLockError:
        raise
    except OSError as exc:
        raise NpxInstallLockError(_unusable(path, _os_reason(exc))) from exc
    usable = False
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise NpxInstallLockError(_unusable(path, "it is not a regular file"))
        if sys.platform == "win32" and info.st_size == 0:
            os.write(fd, b"0")
        usable = True
    except OSError as exc:
        raise NpxInstallLockError(_unusable(path, _os_reason(exc))) from exc
    finally:
        if not usable:
            os.close(fd)
    return fd


def _try_lock(fd: int, path: Path, *, shared: bool) -> bool:
    """Take the lock on *fd* without waiting.

    Args:
        fd: The open lock file.
        path: Its path, for the refusal.
        shared: Whether a shared lock is enough (POSIX only).

    Returns:
        Whether the lock is now held.

    Raises:
        NpxInstallLockError: The filesystem refused the lock for any reason
            other than another holder.
    """
    try:
        if sys.platform == "win32":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            mode = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
            fcntl.flock(fd, mode | fcntl.LOCK_NB)
    except OSError as exc:
        busy = (
            exc.errno in _WINDOWS_BUSY
            if sys.platform == "win32"
            else isinstance(exc, BlockingIOError)
        )
        if busy:
            return False
        if exc.errno in _NO_FILE_LOCKS:
            raise NpxInstallLockError(_no_file_locks(path, exc)) from exc
        raise NpxInstallLockError(
            _unusable(path, f"it cannot be locked: {_os_reason(exc)}"),
        ) from exc
    return True


def _release(fd: int) -> None:
    """Release the lock on *fd* and close it; closing alone also releases.

    Args:
        fd: The open lock file.
    """
    with contextlib.suppress(OSError):
        if sys.platform == "win32":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)


def _is_at_path(fd: int, path: Path) -> bool:
    """Report whether *fd* is still the regular file at *path*.

    Args:
        fd: The open lock file.
        path: Where it was opened.

    Returns:
        False when the file was deleted, moved or replaced, even by a link.
    """
    try:
        named = os.stat(path, follow_symlinks=False)
    except OSError:
        return False
    held = os.fstat(fd)
    return stat.S_ISREG(named.st_mode) and (named.st_dev, named.st_ino) == (
        held.st_dev,
        held.st_ino,
    )


class HeldInstallLock:
    """An install lock lintro holds, which can be checked before use."""

    def __init__(self, fd: int, path: Path) -> None:
        """Remember the locked descriptor.

        Args:
            fd: The open, locked lock file.
            path: Its path.
        """
        self._fd = fd
        self.path = path

    def check(self) -> None:
        """Refuse unless the locked file is still the one at the path.

        Raises:
            NpxInstallLockError: It was deleted or replaced while held.
        """
        if not _is_at_path(self._fd, self.path):
            raise NpxInstallLockError(
                f"lintro's npx install lock {self.path} was deleted or "
                "replaced while lintro held it, so another lintro run could "
                "install the same pin at the same time. lintro did not run "
                "the tool, so its findings are incomplete. Run lintro again, "
                "and do not delete that file while lintro is running.",
            )


def _timeout_message(cache: Path, path: Path, waited: float) -> str:
    """Explain a wait for another run's first install that ran out.

    Args:
        cache: The per-pin cache.
        path: The lock file.
        waited: Seconds waited.

    Returns:
        The message.
    """
    return (
        f"another lintro run was still installing {cache.name} into lintro's "
        f"npm cache {cache} after {waited:.0f}s (lock {path}), so lintro did "
        "not run the tool and its findings are incomplete. Run lintro again "
        "once the other run finishes, or raise the tool's timeout."
    )


def _replaced_message(path: Path, waited: float) -> str:
    """Explain a lock file that never stayed in place long enough to hold.

    Args:
        path: The lock file.
        waited: Seconds spent trying.

    Returns:
        The message.
    """
    return (
        f"lintro's npx install lock {path} kept being replaced: for "
        f"{waited:.1f}s the file lintro locked was no longer the file at that "
        "path (something keeps deleting or replacing it, or the filesystem "
        "reports different identities for the same file). lintro did not run "
        "the tool, so its findings are incomplete. Stop whatever is changing "
        "that file, or put lintro's cache on a local filesystem, then run "
        "lintro again."
    )


def _acquire(cache: Path, path: Path, *, shared: bool, deadline: float) -> int:
    """Lock the file now at *path*, waiting until *deadline* at the latest.

    A file that is no longer at the path once locked (deleted or replaced
    while this run waited) is let go, and the new one is locked instead.

    Args:
        cache: The per-pin cache, for messages.
        path: The lock file.
        shared: Take the lock shared rather than exclusive.
        deadline: :func:`time.monotonic` time when the wait runs out.

    Returns:
        The locked descriptor, which is the file at *path*.

    Raises:
        NpxInstallLockTimeoutError: Another run held the lock until *deadline*.
        NpxInstallLockError: The file locked was still not the one at the path
            at *deadline*.
    """
    started = time.monotonic()
    announced = False
    while True:
        fd = _open(path)
        locked = False
        try:
            while not _try_lock(fd, path, shared=shared):
                now = time.monotonic()
                if now >= deadline:
                    raise NpxInstallLockTimeoutError(
                        _timeout_message(cache, path, now - started),
                    )
                if not announced:
                    announced = True
                    logger.warning(
                        f"Waiting up to {deadline - now:.0f}s for another lintro "
                        f"run to finish installing {cache.name} (lock {path}).",
                    )
                time.sleep(min(_POLL_SECONDS, deadline - now))
            locked = True
        finally:
            if not locked:
                os.close(fd)
        if _is_at_path(fd, path):
            return fd
        _release(fd)
        now = time.monotonic()
        if now >= deadline:
            raise NpxInstallLockError(_replaced_message(path, now - started))
        time.sleep(min(_POLL_SECONDS, deadline - now))


@contextlib.contextmanager
def install_lock(
    cache: Path,
    *,
    shared: bool,
    deadline: float,
) -> Iterator[HeldInstallLock]:
    """Hold the per-pin install lock, waiting until *deadline* at the latest.

    Args:
        cache: The per-pin cache the npx command carries.
        shared: Take the lock shared (check a cache) rather than exclusive
            (install into it).
        deadline: :func:`time.monotonic` time when the wait runs out.

    Yields:
        HeldInstallLock: The lock, held until the context exits, however it
        exits.
    """
    path = install_lock_path(cache)
    fd = _acquire(cache, path, shared=shared, deadline=deadline)
    try:
        yield HeldInstallLock(fd, path)
    finally:
        _release(fd)


@contextlib.contextmanager
def shared_install_lock_now(cache: Path) -> Iterator[bool]:
    """Hold the per-pin lock shared if no first install holds it; never wait.

    Args:
        cache: The per-pin cache.

    Yields:
        bool: Whether the lock is held; False while another run installs, or
        when the lock file was replaced as it was taken.
    """
    path = install_lock_path(cache)
    fd = _open(path)
    try:
        held = _try_lock(fd, path, shared=True) and _is_at_path(fd, path)
        yield held
    finally:
        _release(fd)

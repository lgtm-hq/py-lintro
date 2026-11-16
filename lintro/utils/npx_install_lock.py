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
"""

from __future__ import annotations

import contextlib
import os
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import IO

from loguru import logger

from lintro.utils.npx_integrity import NpxIntegrityError

#: Suffix of the per-pin lock file beside the per-pin cache.
INSTALL_LOCK_SUFFIX: str = ".lintro-install.lock"

#: Pause between attempts to take a lock another process holds.
_POLL_SECONDS: float = 0.05


class NpxInstallLockTimeoutError(NpxIntegrityError):
    """Another lintro run's first install of the pin outlasted the wait."""


def install_lock_path(cache: Path) -> Path:
    """Return the lock file for one per-pin npm cache.

    Args:
        cache: The per-pin cache, ``<lintro cache>/npx/<package>@<pin>``.

    Returns:
        ``<lintro cache>/npx/.<package>@<pin>.lintro-install.lock``.
    """
    return cache.parent / f".{cache.name}{INSTALL_LOCK_SUFFIX}"


def _try_lock(handle: IO[bytes], *, shared: bool) -> bool:
    """Take the lock on *handle* without waiting.

    Args:
        handle: The open lock file.
        shared: Whether a shared lock is enough (POSIX only).

    Returns:
        Whether the lock is now held.
    """
    if sys.platform == "win32":
        import msvcrt

        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    import fcntl

    mode = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
    try:
        fcntl.flock(handle.fileno(), mode | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def _unlock(handle: IO[bytes]) -> None:
    """Release the lock on *handle*.

    Args:
        handle: The open, locked lock file.
    """
    if sys.platform == "win32":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _open(path: Path) -> IO[bytes]:
    """Open, creating if needed, the lock file at *path*.

    Args:
        path: The lock file.

    Returns:
        The open file. On Windows it holds one byte, the region locked.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    if sys.platform == "win32" and os.fstat(handle.fileno()).st_size == 0:
        handle.write(b"0")
        handle.flush()
    return handle


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


@contextlib.contextmanager
def install_lock(
    cache: Path,
    *,
    shared: bool,
    deadline: float,
) -> Iterator[None]:
    """Hold the per-pin install lock, waiting until *deadline* at the latest.

    Args:
        cache: The per-pin cache the npx command carries.
        shared: Take the lock shared (check a cache) rather than exclusive
            (install into it).
        deadline: :func:`time.monotonic` time when the wait runs out.

    Yields:
        None: The lock is held until the context exits, however it exits.

    Raises:
        NpxInstallLockTimeoutError: Another run held the lock until *deadline*.
    """
    path = install_lock_path(cache)
    started = time.monotonic()
    announced = False
    with _open(path) as handle:
        while not _try_lock(handle, shared=shared):
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
        try:
            yield
        finally:
            _unlock(handle)


@contextlib.contextmanager
def shared_install_lock_now(cache: Path) -> Iterator[bool]:
    """Hold the per-pin lock shared if no first install holds it; never wait.

    Args:
        cache: The per-pin cache.

    Yields:
        bool: Whether the lock is held; False while another run installs.
    """
    with _open(install_lock_path(cache)) as handle:
        if not _try_lock(handle, shared=True):
            yield False
            return
        try:
            yield True
        finally:
            _unlock(handle)

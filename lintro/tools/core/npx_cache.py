"""lintro's own npm cache for npx fallbacks, kept honest before each run (#2043).

Every npx fallback runs with ``--cache=<lintro cache>/npx/<package>@<pin>``.
npm installs the pinned package under ``<cache>/_npx/<hash>`` and, on later
runs, reuses whatever that entry holds when it considers the pin installed.
An entry whose top-level package is not the pin, for example a tampered or
half-written one, would then run a different version behind a version gate
that reported the pin. Before the gate trusts the pin, lintro therefore
validates every entry and deletes any that would not run the pin, so npx
reinstalls it.

Validation takes npm's own per-entry lock (``<entry>/concurrency.lock``, a
directory created with ``mkdir``, as in ``libnpmexec``'s ``with-lock.js``), so
it never inspects or removes an entry while npx is reading or installing it.

lintro never reclaims, moves or deletes a lock it did not create in this
process (#3422). A lock that is already there is waited for; if it is
abandoned, or still held after the wait, the repair is refused with the exact
path to delete. lintro's own lock is an empty directory, as npm's is, so npm's
own stale-lock handling cleans up after a lintro run that was killed while
holding one. lintro remembers the inode and mtime of the directory it created,
releases it by renaming it to a unique name, and removes it only if the
renamed directory is that one. It re-checks that identity right before moving
an entry, and refuses if the lock is no longer its own.

Limitation: npm treats a lock untouched for 60 seconds as abandoned. If a
lintro run holds its lock that long, for example because it was suspended,
npm may take the lock over. lintro's release then finds npm's lock aside and
puts it back with a rename that never replaces an existing name, where the
platform has one (macOS, Linux, Windows); otherwise it leaves npm's lock under
the aside name, logs it, and npm reports the compromised lock. The ownership
re-check before a move refuses in that case, so lintro never moves an entry
npm has taken over.

A bad entry is renamed aside atomically, while lintro holds its lock, under a
bounded unique name built from its original name, and then removed. The lock
travels with the entry. An entry that is not a directory is unlinked in place
rather than moved, so a directory npx has meanwhile created under that name
is never moved unlocked. An entry already aside, inside ``_npx`` or beside
it, is deleted in place and never renamed again, so concurrent repairs cannot
grow names (#3419); an entry another repair already moved or removed counts
as repaired.

Any failure to lock, validate or remove an entry npx could run is reported,
and the caller refuses the fallback. A leftover aside that cannot be deleted
only logs a warning: it has no npm entry name, so it cannot change what runs.
"""

from __future__ import annotations

import contextlib
import ctypes
import errno
import json
import os
import shutil
import stat
import sys
import time
import uuid
from pathlib import Path

from loguru import logger

#: npm treats a lock whose mtime is older than this as abandoned.
_STALE_LOCK_SECONDS: float = 60.0

#: How long to wait for a live lock held by a concurrent npx or lintro.
_LOCK_WAIT_SECONDS: float = 30.0

#: Prefix for entries renamed aside before deletion.
_STALE_PREFIX: str = ".lintro-stale-"

#: Characters of the original name kept in an aside name, for debugging.
_ASIDE_BASE_CHARS: int = 32

#: Times one entry is re-examined when it keeps changing type under a repair.
_ENTRY_ATTEMPTS: int = 5

#: Identity of a lock directory lintro created: inode and mtime.
_LockId = tuple[int, int]

#: ``renamex_np`` flag on macOS: fail when the destination exists.
_RENAME_EXCL: int = 0x4

#: ``renameat2`` flag on Linux: fail when the destination exists.
_RENAME_NOREPLACE: int = 0x1

#: ``AT_FDCWD`` on Linux: resolve relative paths against the working directory.
_AT_FDCWD: int = -100


class NpxCacheError(Exception):
    """lintro's npm cache cannot be trusted or repaired."""


def npx_cache_root() -> Path:
    """Return the directory that holds lintro's per-pin npm caches.

    Returns:
        ``<lintro cache root>/npx``.
    """
    from lintro.utils import file_cache

    return file_cache.CACHE_DIR / "npx"


def npx_cache_dir(spec: str) -> Path:
    """Return the npm cache lintro uses for one pinned package.

    One cache per ``package@pin`` keeps each ``_npx`` tree about a single spec,
    which keeps validation simple.

    Args:
        spec: Pinned npm spec, such as ``prettier@3.9.4`` or
            ``@commitlint/cli@20.0.0``.

    Returns:
        ``<lintro cache root>/npx/<spec>``, with ``/`` in scoped names
        replaced by ``+``.
    """
    return npx_cache_root() / spec.replace("/", "+")


def _within(path: Path, root: Path) -> bool:
    """Report whether *path* is *root* or lies below it.

    Args:
        path: Resolved path to test.
        root: Resolved containing directory.

    Returns:
        True when *path* is inside *root*.
    """
    return path == root or root in path.parents


def check_cache_location(cache: Path, *, pathsep: str = os.pathsep) -> Path:
    """Resolve a cache directory and require it to be safe for npm.

    Args:
        cache: Cache directory lintro passes as ``--cache``.
        pathsep: ``PATH`` separator of the platform npx runs on.

    Returns:
        The cache's real path.

    Raises:
        NpxCacheError: The cache escapes lintro's cache root, contains the
            ``PATH`` separator, or is not a directory.
    """
    from lintro.utils import file_cache

    root = Path(os.path.realpath(file_cache.CACHE_DIR))
    real = Path(os.path.realpath(cache))
    if not _within(real, root) or real == root:
        raise NpxCacheError(
            f"lintro's npm cache {cache} resolves to {real}, outside {root}",
        )
    # npm prepends ``<cache>/_npx/<hash>/node_modules/.bin`` to PATH.
    if pathsep in str(cache) or pathsep in str(real):
        raise NpxCacheError(
            f"lintro's npm cache path {cache} contains the PATH separator {pathsep!r}",
        )
    if real.exists() and not real.is_dir():
        raise NpxCacheError(f"lintro's npm cache path {cache} is not a directory")
    return real


def _rename_noreplace(src: Path, dst: Path) -> bool | None:
    """Rename *src* to *dst* only if *dst* does not exist, atomically.

    Used only to put back a lock npm took while lintro's release had it
    aside. POSIX ``rename`` silently replaces an empty directory, which could
    be yet another lock npm has just taken, so a plain rename is never used.

    Args:
        src: Path to move.
        dst: Destination that must not exist.

    Returns:
        True when moved, False when *dst* exists, None when this platform or
        filesystem offers no such rename.

    Raises:
        OSError: The rename failed for another reason.
    """
    if sys.platform == "win32":
        try:
            os.rename(src, dst)
        except FileExistsError:
            return False
        return True
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        if sys.platform == "darwin":
            result = libc.renamex_np(
                os.fsencode(src),
                os.fsencode(dst),
                ctypes.c_uint(_RENAME_EXCL),
            )
        else:
            result = libc.renameat2(
                _AT_FDCWD,
                os.fsencode(src),
                _AT_FDCWD,
                os.fsencode(dst),
                ctypes.c_uint(_RENAME_NOREPLACE),
            )
    except (AttributeError, OSError):
        return None
    if result == 0:
        return True
    code = ctypes.get_errno()
    if code in {errno.EEXIST, errno.ENOTEMPTY}:
        return False
    if code in {errno.EINVAL, errno.ENOSYS, errno.ENOTSUP}:
        return None
    raise OSError(code, os.strerror(code), str(src))


def _identity(path: Path) -> _LockId:
    """Return the identity lintro tracks for a lock directory.

    Inode and mtime, not ctime: a rename changes the moved directory's ctime
    but keeps both of these, and npm changes the mtime only of a lock it
    holds itself.

    Args:
        path: Lock directory, under its own or an aside name.

    Returns:
        ``(st_ino, st_mtime_ns)``.
    """
    info = path.stat()
    return (info.st_ino, info.st_mtime_ns)


def _not_ours(lock: Path, why: str) -> str:
    """Explain the refusal for a lock lintro did not create.

    Args:
        lock: ``<entry>/concurrency.lock``.
        why: What lintro found.

    Returns:
        A message naming the exact path to delete.
    """
    return (
        f"lintro's npm cache lock {lock} {why}; lintro never removes a lock it "
        f"did not create. Delete {lock} once no npm or npx process is running."
    )


def _acquire(lock: Path) -> _LockId:
    """Take npm's ``mkdir`` lock for one ``_npx`` entry.

    The lock is an empty directory, exactly as npm makes one, so npm's own
    stale-lock handling cleans up after a lintro run that was killed while
    holding it. Ownership is the created directory's identity, kept here.

    A ``FileNotFoundError`` (or ``NotADirectoryError``) from ``mkdir``
    propagates when the entry itself has gone or changed type, which means a
    concurrent repair or npx got there first.

    Args:
        lock: ``<entry>/concurrency.lock``.

    Returns:
        The identity of the lock directory created, for :func:`_release`.

    Raises:
        NpxCacheError: Another process's lock is abandoned, or stayed held
            past the wait. lintro never removes it.
    """
    deadline = time.monotonic() + _LOCK_WAIT_SECONDS
    while True:
        try:
            lock.mkdir()
        except FileExistsError:
            pass
        else:
            return _identity(lock)
        try:
            age = _lock_age(lock)
        except FileNotFoundError:
            continue
        if age > _STALE_LOCK_SECONDS:
            raise NpxCacheError(_not_ours(lock, "looks abandoned")) from None
        if time.monotonic() > deadline:
            raise NpxCacheError(_not_ours(lock, "stayed locked")) from None
        time.sleep(0.1)


def _lock_age(lock: Path) -> float:
    """Return how long ago a lock was created or last touched by its holder.

    Args:
        lock: ``<entry>/concurrency.lock``.

    Returns:
        Seconds since the lock's mtime.
    """
    return time.time() - lock.stat().st_mtime


def _holds(lock: Path, held: _LockId) -> bool:
    """Report whether the lock at *lock* is still the one lintro created.

    Args:
        lock: ``<entry>/concurrency.lock``.
        held: Identity returned by :func:`_acquire`.

    Returns:
        True when the directory there has lintro's inode and mtime.
    """
    try:
        return _identity(lock) == held
    except OSError:
        return False


def _release(lock: Path, held: _LockId) -> None:
    """Remove lintro's lock, checking it after an atomic move aside.

    The lock is renamed to a unique name and checked there, so a check that
    passes is about the directory that will be removed. A lock npm took can
    only be there when lintro held its own past npm's 60 second threshold;
    it is put back with an exclusive rename where the platform has one, and
    otherwise left under the aside name and logged.

    Args:
        lock: ``<entry>/concurrency.lock``.
        held: Identity returned by :func:`_acquire`.
    """
    if not _holds(lock, held):
        logger.debug(f"lintro no longer holds {lock}; leaving it in place")
        return
    aside = lock.with_name(_aside_name(lock.name))
    try:
        os.rename(lock, aside)
    except OSError:
        return  # Nothing there to release.
    try:
        ours = _identity(aside) == held
    except OSError:
        return
    if ours:
        with contextlib.suppress(OSError):
            aside.rmdir()
        return
    try:
        restored = _rename_noreplace(aside, lock)
    except OSError:
        restored = False
    if not restored:
        logger.warning(
            f"lintro held its npm cache lock {lock} past npm's stale threshold "
            f"and npm took it over; npm's lock is left at {aside}",
        )


def _bin_belongs_to(entry: Path, package_dir: Path, binary: str, package: str) -> bool:
    """Report whether ``node_modules/.bin/<binary>`` runs *package_dir*.

    Args:
        entry: The ``_npx`` entry.
        package_dir: ``<entry>/node_modules/<package>``.
        binary: Executable name.
        package: npm package name.

    Returns:
        True when the link (or Windows shim) points into the package.
    """
    bin_dir = entry / "node_modules" / ".bin"
    if sys.platform == "win32":
        shim = bin_dir / f"{binary}.cmd"
        try:
            text = shim.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        marker = "\\..\\" + package.replace("/", "\\") + "\\"
        return marker in text
    link = bin_dir / binary
    if not link.is_file():
        return False
    target = Path(os.path.realpath(link))
    return _within(target, Path(os.path.realpath(package_dir)))


def entry_runs_pin(entry: Path, *, package: str, pin: str, binary: str) -> bool:
    """Report whether npx would run exactly the pin from this ``_npx`` entry.

    Args:
        entry: ``<cache>/_npx/<hash>``.
        package: npm package name.
        pin: Exact pinned version.
        binary: Executable name.

    Returns:
        True when the top-level package is a real directory holding exactly
        *package* at *pin*, and its executable link points into it.
    """
    package_dir = entry / "node_modules" / package
    if package_dir.is_symlink() or not package_dir.is_dir():
        return False
    try:
        manifest = json.loads(
            (package_dir / "package.json").read_text(encoding="utf-8"),
        )
    except (OSError, ValueError):
        return False
    if not isinstance(manifest, dict):
        return False
    if manifest.get("name") != package or manifest.get("version") != pin:
        return False
    return _bin_belongs_to(entry, package_dir, binary, package)


def _aside_name(name: str) -> str:
    """Build a unique name, of bounded length, for an entry set aside.

    Args:
        name: The entry's current name.

    Returns:
        ``.lintro-stale-<base>-<uuid>``, where *base* is at most
        :data:`_ASIDE_BASE_CHARS` characters of the original name with any
        stale prefixes removed, so the result never grows with *name*.
    """
    base = name
    while base.startswith(_STALE_PREFIX):
        base = base[len(_STALE_PREFIX) :]
    return f"{_STALE_PREFIX}{base[:_ASIDE_BASE_CHARS]}-{uuid.uuid4().hex}"


def _delete(path: Path) -> None:
    """Delete a path set aside, which nothing will run again.

    A link is removed itself, never followed. Parts another repair removes
    first are not failures. A leftover that stays is logged, not raised: it
    has no npm entry name, so it cannot change what runs, and the next repair
    tries again.

    Args:
        path: Entry already set aside, or a moved-aside name.
    """
    failures: list[str] = []

    def record(_func: object, failed: str, exc: BaseException) -> None:
        if not isinstance(exc, FileNotFoundError):
            failures.append(f"{failed}: {exc}")

    if path.is_dir() and not path.is_symlink():
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=record)
        else:  # pragma: no cover - exercised on the 3.11 CI leg
            shutil.rmtree(path, onerror=lambda f, p, info: record(f, p, info[1]))
    else:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            failures.append(f"{path}: {exc}")
    if failures and (path.exists() or path.is_symlink()):
        logger.warning(
            f"lintro could not delete the npm cache leftover {path} "
            f"({failures[0]}); it is not run and will be retried",
        )


def _discard(entry: Path) -> bool:
    """Rename an entry aside atomically, then delete it.

    The caller holds the entry's lock, which moves aside with it.

    Args:
        entry: Locked ``_npx`` entry to remove.

    Returns:
        True when this call moved the entry (and the lock in it) aside;
        False when it was already gone.

    Raises:
        NpxCacheError: The entry is still in place and could not be moved
            aside.
    """
    aside = entry.with_name(_aside_name(entry.name))
    try:
        os.replace(entry, aside)
    except OSError as exc:
        # A missing source means another repair moved or removed it. If the
        # name exists again, npx may have recreated it after that move and
        # before this rename failed; refusing then is rare and errs closed,
        # and the next run validates the new entry.
        if not (entry.exists() or entry.is_symlink()):
            return False
        raise NpxCacheError(f"cannot remove npm cache entry {entry}: {exc}") from exc
    _delete(aside)
    return True


def _clear_unless_directory(path: Path) -> bool:
    """Unlink *path* in place unless it is a real directory.

    ``unlink`` never removes a directory, so a directory npx creates under the
    same name after the check is left for the locked path instead of being
    moved while npx writes it.

    Args:
        path: An ``_npx`` entry, or ``_npx`` itself.

    Returns:
        True when a real directory is there to validate; False when nothing is.

    Raises:
        NpxCacheError: A link or file kept its place and could not be removed.
    """
    failure: OSError | None = None
    for _ in range(_ENTRY_ATTEMPTS):
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            return False
        if stat.S_ISDIR(mode):
            return True
        try:
            os.unlink(path)
        except FileNotFoundError:
            return False
        except OSError as exc:
            failure = exc  # Look again: it may have become a directory.
        else:
            return False
    raise NpxCacheError(f"cannot remove npm cache entry {path}: {failure}")


def _repair_entry(entry: Path, *, package: str, pin: str, binary: str) -> None:
    """Keep one ``_npx`` entry if it runs the pin, otherwise remove it.

    Args:
        entry: ``<cache>/_npx/<hash>``.
        package: npm package name.
        pin: Exact pinned version.
        binary: Executable name.

    Raises:
        NpxCacheError: The entry could not be locked or removed, or kept
            changing under the repair.
    """
    lock = entry / "concurrency.lock"
    for _ in range(_ENTRY_ATTEMPTS):
        if not _clear_unless_directory(entry):
            return
        try:
            held = _acquire(lock)
        except (FileNotFoundError, NotADirectoryError):
            continue  # Removed or replaced meanwhile: look again.
        if os.path.realpath(entry) != str(entry):
            # Replaced by a link after the check: the lock landed elsewhere.
            _release(lock, held)
            continue
        moved = False
        try:
            if not entry_runs_pin(entry, package=package, pin=pin, binary=binary):
                if not _holds(lock, held):
                    raise NpxCacheError(
                        f"lintro lost its lock {lock} on npm cache entry {entry}; "
                        "it was held past npm's 60 second stale threshold",
                    )
                moved = _discard(entry)
        finally:
            if not moved:
                _release(lock, held)
        return
    raise NpxCacheError(f"lintro's npm cache entry {entry} kept changing")


def _delete_leftovers(directory: Path) -> None:
    """Delete everything set aside directly inside *directory*.

    Args:
        directory: The per-pin cache, already resolved and checked to lie
            inside lintro's cache root, so every child is inside it too.
    """
    for leftover in sorted(directory.iterdir()):
        if leftover.name.startswith(_STALE_PREFIX):
            _delete(leftover)


def repair_npx_cache(
    cache: Path,
    *,
    package: str,
    pin: str,
    binary: str,
    pathsep: str = os.pathsep,
) -> None:
    """Make every ``_npx`` entry in lintro's cache either run the pin or vanish.

    Args:
        cache: Per-pin cache from :func:`npx_cache_dir`.
        package: npm package name.
        pin: Exact pinned version.
        binary: Executable name.
        pathsep: ``PATH`` separator of the platform npx runs on.

    An unsafe cache, or an entry that cannot be locked or repaired, raises
    :class:`NpxCacheError` from the helpers called here.
    """
    real = check_cache_location(cache, pathsep=pathsep)
    if not real.is_dir():
        return
    # A whole ``_npx`` set aside by an earlier release lands beside it.
    _delete_leftovers(real)
    npx_root = real / "_npx"
    if not _clear_unless_directory(npx_root):
        return
    for entry in sorted(npx_root.iterdir()):
        if entry.name.startswith(_STALE_PREFIX):
            # Already aside, so no npx run uses it: delete it where it is.
            _delete(entry)
        else:
            _repair_entry(entry, package=package, pin=pin, binary=binary)

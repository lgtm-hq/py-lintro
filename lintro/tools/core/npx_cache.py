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
A bad entry is renamed aside atomically and then removed. Any failure to
lock, validate or repair is reported, and the caller refuses the fallback.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
import uuid
from pathlib import Path

#: npm treats a lock whose mtime is older than this as abandoned.
_STALE_LOCK_SECONDS: float = 60.0

#: How long to wait for a live lock held by a concurrent npx or lintro.
_LOCK_WAIT_SECONDS: float = 30.0

#: Prefix for entries renamed aside before deletion.
_STALE_PREFIX: str = ".lintro-stale-"


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


def _acquire(lock: Path) -> None:
    """Take npm's ``mkdir`` lock for one ``_npx`` entry.

    A ``FileNotFoundError`` from ``mkdir`` propagates when the entry itself
    has gone, which means a concurrent repair already removed it.

    Args:
        lock: ``<entry>/concurrency.lock``.

    Raises:
        NpxCacheError: The lock stayed held by a live process.
    """
    deadline = time.monotonic() + _LOCK_WAIT_SECONDS
    while True:
        try:
            lock.mkdir()
            return
        except FileExistsError:
            try:
                age = time.time() - lock.stat().st_mtime
            except FileNotFoundError:
                continue
            if age > _STALE_LOCK_SECONDS:
                # Abandoned, as npm itself would judge it.
                shutil.rmtree(lock, ignore_errors=True)
                continue
            if time.monotonic() > deadline:
                raise NpxCacheError(
                    f"lintro's npm cache entry {lock.parent} stayed locked",
                ) from None
            time.sleep(0.1)


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


def _discard(entry: Path) -> None:
    """Rename an entry aside atomically, then delete it.

    Args:
        entry: Entry (or stray file) to remove.

    Raises:
        NpxCacheError: The entry could not be moved aside.
    """
    aside = entry.with_name(f"{_STALE_PREFIX}{entry.name}-{uuid.uuid4().hex}")
    try:
        os.replace(entry, aside)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise NpxCacheError(f"cannot remove npm cache entry {entry}: {exc}") from exc
    if aside.is_dir() and not aside.is_symlink():
        shutil.rmtree(aside, ignore_errors=True)
    else:
        aside.unlink(missing_ok=True)


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
    npx_root = real / "_npx"
    if npx_root.is_symlink() or (npx_root.exists() and not npx_root.is_dir()):
        _discard(npx_root)
        return
    if not npx_root.is_dir():
        return
    for entry in sorted(npx_root.iterdir()):
        if (
            entry.name.startswith(_STALE_PREFIX)
            or entry.is_symlink()
            or not entry.is_dir()
        ):
            _discard(entry)
            continue
        lock = entry / "concurrency.lock"
        try:
            _acquire(lock)
        except FileNotFoundError:
            continue  # Removed by a concurrent repair.
        try:
            if not entry_runs_pin(entry, package=package, pin=pin, binary=binary):
                _discard(entry)
        finally:
            if lock.exists():
                shutil.rmtree(lock, ignore_errors=True)

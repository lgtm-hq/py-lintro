"""Check what npx would run from lintro's cache against the lockfile (#3406).

lintro's npx fallback cache (:mod:`lintro.tools.core.npx_cache`) checks that
the top-level package is the pin. That alone does not stop a file inside the
install from being replaced while ``package.json`` keeps the pinned version.
This module checks the files themselves.

**Contents.** npx writes a ``package-lock.json`` into every ``_npx/<hash>``
entry, with the Subresource Integrity hash of each package's tarball, and npm
keeps those tarballs content-addressed in the same cache
(``<cache>/_cacache/content-v2/<algorithm>/<hex>``). An entry is accepted only
when, for every package its lockfile lists:

- the tarball is in the cache and hashes to the lockfile's ``integrity``;
- every regular file in the tarball is installed with the same bytes, as a
  regular file with one link (a hard link could be rewritten through its other
  name later);
- the package directory holds no file or symlink the tarball lacks. A package
  whose lockfile entry says ``hasInstallScript`` is exempt from this check, since
  its install script may legitimately add files.

Each ``node_modules`` directory may hold only lockfile packages, plus
``.package-lock.json`` at the top. Its ``.bin`` may hold only the executables
those packages declare, and on POSIX they must be links to the declared files.
The top-level ``.bin`` is on the tool's ``PATH``, so an extra ``.bin/node``
would otherwise replace the ``node`` that the tool's ``#!/usr/bin/env node``
starts.

**Binding the check to the run.** An entry's fingerprint is the type, inode,
size, mtime and ctime of everything in it. Users can set mtime but not ctime,
so on POSIX no file can be written, replaced or put back without changing it.
The fingerprint leaves out npm's ``concurrency.lock``, lintro's own lock asides,
and the times of the entry directory, all of which change when a lock is
taken. A fingerprint is taken before and after the content check, and the
check counts only when the two match. Verified fingerprints are remembered for
the life of the process, so an unchanged entry is not hashed twice.

Before lintro spawns an npx fallback, it checks every entry in the command's
``--cache``. An entry whose fingerprint was already verified passes at once;
any other entry is verified again. After the process exits, every entry must
still have the fingerprint it had before the spawn. An entry that appeared
during the run, which is npx's first install of the pin, is verified then. Any
failure raises :class:`NpxIntegrityError`, which lintro reports as an
execution failure with incomplete findings. It is never treated as a pass and
never produces findings.

**Limits.** The lockfile and the tarballs live in the same user-writable
cache as the install. This check catches a partial change, such as the one in
#3406 where ``bin.js`` was replaced and ``package.json`` left alone. It does
not stop a user who rewrites the lockfile, the tarball and the install so that
they agree. That user already controls lintro's cache, which lintro trusts as
it trusts its own installation. A cold run executes npm's fresh install, which
npm checks against the registry's integrity hashes; lintro verifies it once
the run has finished. On Windows, ``st_ctime`` is the creation time, so there
the fingerprint relies on size, mtime and inode.
"""

from __future__ import annotations

import base64
import binascii
import gzip
import hashlib
import io
import json
import os
import stat
import tarfile
import threading
import zlib
from collections.abc import Mapping
from pathlib import Path, PurePosixPath

#: Prefix lintro gives entries and locks it has set aside before deletion.
STALE_PREFIX: str = ".lintro-stale-"

#: npm's per-entry lock directory, taken around every check or install.
NPM_LOCK_NAME: str = "concurrency.lock"

#: Integrity algorithms lintro checks, strongest first.
_ALGORITHMS: tuple[str, ...] = ("sha512", "sha384", "sha256", "sha1")

#: First bytes of a gzip stream; npm package tarballs are gzipped.
_GZIP_MAGIC: bytes = b"\x1f\x8b"

#: Windows ``.bin`` shim suffixes npm writes beside each executable.
_SHIM_SUFFIXES: tuple[str, ...] = (".cmd", ".ps1")

#: One path in an entry: relative path, file type, inode, size, mtime, ctime.
_Stat = tuple[str, int, int, int, int, int]

#: Everything in one ``_npx`` entry, sorted by path.
Fingerprint = tuple[_Stat, ...]

#: Fingerprints whose contents were verified, by (cache, entry name).
_VERIFIED: dict[tuple[str, str], Fingerprint] = {}
_VERIFIED_LOCK = threading.Lock()


class NpxIntegrityError(RuntimeError):
    """What npx would run from lintro's cache does not match its lockfile."""


def forget_verified() -> None:
    """Drop every remembered verification, so the next check hashes again."""
    with _VERIFIED_LOCK:
        _VERIFIED.clear()


def _stat_row(path: str, rel: str) -> _Stat:
    """Describe one path for a fingerprint, without following links.

    Args:
        path: Absolute path.
        rel: Path relative to the entry.

    Returns:
        Its relative path, file type, inode, size, mtime and ctime.
    """
    info = os.lstat(path)
    return (
        rel,
        stat.S_IFMT(info.st_mode),
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def entry_fingerprint(entry: Path) -> Fingerprint:
    """Fingerprint one ``_npx`` entry.

    Args:
        entry: ``<cache>/_npx/<hash>``.

    Returns:
        Sorted rows for everything in the entry. The entry directory itself
        contributes only its type and inode; npm's lock and lintro's lock
        asides at its top are left out. An ``OSError`` reading the entry
        propagates.
    """
    top = os.lstat(entry)
    rows: list[_Stat] = [(".", stat.S_IFMT(top.st_mode), top.st_ino, 0, 0, 0)]
    if not stat.S_ISDIR(top.st_mode):
        return tuple(rows)
    pending = [(str(entry), "")]
    while pending:
        directory, prefix = pending.pop()
        with os.scandir(directory) as children:
            for child in children:
                if not prefix and (
                    child.name == NPM_LOCK_NAME or child.name.startswith(STALE_PREFIX)
                ):
                    continue
                rel = f"{prefix}{child.name}"
                rows.append(_stat_row(child.path, rel))
                if child.is_dir(follow_symlinks=False):
                    pending.append((child.path, f"{rel}/"))
    return tuple(sorted(rows))


def cache_entries(cache: Path) -> list[Path]:
    """List the ``_npx`` entries npx could run from a cache.

    Args:
        cache: The per-pin cache, already resolved.

    Returns:
        Every child of ``_npx`` except those lintro has set aside.

    Raises:
        NpxIntegrityError: ``_npx`` is there but is not a real directory.
    """
    npx_root = cache / "_npx"
    try:
        mode = os.lstat(npx_root).st_mode
    except FileNotFoundError:
        return []
    if not stat.S_ISDIR(mode):
        raise NpxIntegrityError(f"lintro's npm cache {npx_root} is not a directory")
    return sorted(
        child for child in npx_root.iterdir() if not child.name.startswith(STALE_PREFIX)
    )


def _strongest_hash(integrity: object) -> tuple[str, bytes] | None:
    """Pick the strongest supported hash from a Subresource Integrity string.

    Args:
        integrity: The lockfile's ``integrity`` value.

    Returns:
        The algorithm and the raw digest, or None when there is no usable one.
    """
    if not isinstance(integrity, str):
        return None
    found: dict[str, bytes] = {}
    for token in integrity.split():
        algorithm, _, encoded = token.partition("-")
        if algorithm not in _ALGORITHMS:
            continue
        try:
            found[algorithm] = base64.b64decode(
                encoded.split("?", 1)[0],
                validate=True,
            )
        except (binascii.Error, ValueError):
            continue
    for algorithm in _ALGORITHMS:
        if algorithm in found:
            return algorithm, found[algorithm]
    return None


def _tarball(cache: Path, integrity: object) -> bytes | str:
    """Read a package tarball from npm's content cache and check its hash.

    Args:
        cache: The per-pin cache.
        integrity: The lockfile's ``integrity`` value for the package.

    Returns:
        The tarball bytes, or why they cannot be trusted.
    """
    picked = _strongest_hash(integrity)
    if picked is None:
        return "has no usable integrity hash in its lockfile"
    algorithm, digest = picked
    hexdigest = digest.hex()
    path = (
        cache
        / "_cacache"
        / "content-v2"
        / algorithm
        / hexdigest[:2]
        / hexdigest[2:4]
        / hexdigest[4:]
    )
    try:
        data = path.read_bytes()
    except OSError:
        return "has no tarball in lintro's npm cache to check it against"
    if hashlib.new(algorithm, data).digest() != digest:
        return "has a cached tarball that does not match its lockfile integrity"
    return data


def _published_files(data: bytes) -> dict[str, bytes] | str:
    """List the regular files npm extracts from a package tarball.

    pacote drops the first path component and every link, and ignores
    unsafe paths. When a name repeats, the last member wins, as on
    extraction.

    Args:
        data: Tarball bytes, already checked against the lockfile.

    Returns:
        File contents by path relative to the package, or why the tarball
        cannot be read.
    """
    files: dict[str, bytes] = {}
    ignores: set[str] = set()
    try:
        # Inflating once up front is several times faster than letting
        # tarfile seek through the gzip stream member by member.
        raw = gzip.decompress(data) if data[:2] == _GZIP_MAGIC else data
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
            for member in archive:
                if not member.isfile():
                    continue
                name = PurePosixPath(member.name.replace("\\", "/"))
                parts = name.parts[1:]
                if name.is_absolute() or not parts or ".." in parts:
                    continue
                rel = "/".join(parts)
                # pacote installs a packed ``.gitignore`` as ``.npmignore``,
                # and drops it when a ``.npmignore`` beside it came first.
                if parts[-1] == ".npmignore":
                    ignores.add(rel)
                elif parts[-1] == ".gitignore":
                    rel = rel.removesuffix(".gitignore") + ".npmignore"
                    if rel in ignores:
                        continue
                extracted = archive.extractfile(member)
                if extracted is not None:
                    files[rel] = extracted.read()
    except (tarfile.TarError, OSError, EOFError, zlib.error):
        return "has a cached tarball that cannot be read"
    return files


def _installed_matches(directory: Path, files: Mapping[str, bytes]) -> str | None:
    """Compare installed files with the published ones.

    Args:
        directory: The installed package directory.
        files: Published contents by relative path.

    Returns:
        Why the install differs, or None.
    """
    for rel, published in files.items():
        path = directory / rel
        try:
            info = os.lstat(path)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                return f"has {rel} installed as something other than its own file"
            if path.read_bytes() != published:
                return f"has {rel} changed from the published package"
        except OSError:
            return f"is missing {rel}"
    return None


def _unpublished_file(directory: Path, files: Mapping[str, bytes]) -> str | None:
    """Find a file or link in a package that its tarball does not hold.

    The package's own ``node_modules`` is skipped: its children are other
    lockfile packages, checked by :func:`_tree_problem`.

    Args:
        directory: The installed package directory.
        files: Published contents by relative path.

    Returns:
        The first unexpected path, or None.
    """
    for root, dirs, names in os.walk(directory):
        rel_root = Path(root).relative_to(directory).as_posix()
        prefix = "" if rel_root == "." else f"{rel_root}/"
        if not prefix and "node_modules" in dirs:
            dirs.remove("node_modules")
        for name in dirs:
            if os.path.islink(os.path.join(root, name)):
                return f"{prefix}{name}"
        for name in names:
            if f"{prefix}{name}" not in files:
                return f"{prefix}{name}"
    return None


def _package_problem(
    cache: Path,
    directory: Path,
    meta: Mapping[str, object],
) -> str | None:
    """Check one installed package against its lockfile entry.

    Args:
        cache: The per-pin cache.
        directory: The installed package directory.
        meta: Its lockfile entry.

    Returns:
        Why the package cannot be trusted, or None.
    """
    data = _tarball(cache, meta.get("integrity"))
    if isinstance(data, str):
        return data
    files = _published_files(data)
    if isinstance(files, str):
        return files
    reason = _installed_matches(directory, files)
    if reason is not None or meta.get("hasInstallScript"):
        return reason
    extra = _unpublished_file(directory, files)
    if extra is not None:
        return f"has {extra}, which is not in the published package"
    return None


def _declared_bins(
    entry: Path,
    prefix: str,
    installed: Mapping[str, Mapping[str, object]],
) -> dict[str, set[str]]:
    """Map the executables the packages in one ``node_modules`` declare.

    Args:
        entry: The ``_npx`` entry.
        prefix: Lockfile key prefix of the directory, such as
            ``node_modules/``.
        installed: Installed lockfile entries by key.

    Returns:
        Executable name to the real paths it may link to.
    """
    bins: dict[str, set[str]] = {}
    for key, meta in installed.items():
        rest = key[len(prefix) :] if key.startswith(prefix) else None
        if rest is None or "/node_modules/" in f"/{rest}/":
            continue
        declared = meta.get("bin")
        if not isinstance(declared, dict):
            continue
        for name, target in declared.items():
            if isinstance(target, str):
                real = os.path.realpath(entry / key / target)
                bins.setdefault(str(name), set()).add(real)
    return bins


def _bin_problem(
    bin_dir: Path,
    bins: Mapping[str, set[str]],
    *,
    windows: bool,
) -> str | None:
    """Check that a ``.bin`` directory holds only declared executables.

    Args:
        bin_dir: ``<node_modules>/.bin``.
        bins: Declared executable names and their real targets.
        windows: Whether npm wrote Windows shims rather than links.

    Returns:
        Why the directory cannot be trusted, or None.
    """
    if bin_dir.is_symlink() or not bin_dir.is_dir():
        return f"has {bin_dir.name} that is not a directory"
    for child in bin_dir.iterdir():
        name = child.name
        if windows:
            for suffix in _SHIM_SUFFIXES:
                name = name.removesuffix(suffix)
            if name not in bins:
                return f"has .bin/{child.name}, which no locked package declares"
            continue
        if name not in bins:
            return f"has .bin/{child.name}, which no locked package declares"
        if not child.is_symlink() or os.path.realpath(child) not in bins[name]:
            return f"has .bin/{child.name} pointing outside the package declaring it"
    return None


def _tree_problem(
    entry: Path,
    installed: Mapping[str, Mapping[str, object]],
    *,
    windows: bool,
) -> str | None:
    """Check that every ``node_modules`` holds only what the lockfile installed.

    Args:
        entry: The ``_npx`` entry.
        installed: Installed lockfile entries by key.
        windows: Whether npm wrote Windows shims rather than links.

    Returns:
        Why the tree cannot be trusted, or None.
    """
    prefixes = ["node_modules/"] + [
        f"{key}/node_modules/"
        for key in installed
        if (entry / key / "node_modules").is_dir()
    ]
    for prefix in prefixes:
        directory = entry / prefix
        if directory.is_symlink() or not directory.is_dir():
            continue
        for child in directory.iterdir():
            if child.name == ".bin":
                reason = _bin_problem(
                    child,
                    _declared_bins(entry, prefix, installed),
                    windows=windows,
                )
                if reason is not None:
                    return reason
            elif child.name == ".package-lock.json" and prefix == "node_modules/":
                continue
            elif child.name.startswith("@") and not child.is_symlink():
                for scoped in child.iterdir():
                    key = f"{prefix}{child.name}/{scoped.name}"
                    if key not in installed:
                        return f"has {key}, not in its lockfile"
            elif f"{prefix}{child.name}" not in installed:
                return f"has {prefix}{child.name}, not in its lockfile"
    return None


def _entry_key(key: str, *, npm_entry: str, real_entry: str) -> str | None:
    """Turn a lockfile key into a path below the entry.

    npm writes each key relative to the entry path it was given. When that
    path runs through a symlink (``/tmp`` on macOS, say), npm names packages
    by their real path, so the key climbs out with ``..`` and back down to
    the real entry.

    Args:
        key: Key from the lockfile's ``packages``.
        npm_entry: The entry's path as npm was given it, absolute.
        real_entry: The entry's real path.

    Returns:
        ``node_modules/...`` below the entry, or None when the key names
        anything else.
    """
    located = os.path.normpath(os.path.join(npm_entry, key))
    for base in (os.path.normpath(npm_entry), real_entry):
        if located.startswith(base + os.sep):
            rest = Path(located[len(base) + 1 :]).as_posix()
            return rest if rest.startswith("node_modules/") else None
    return None


def _installed_packages(
    entry: Path,
    npm_entry: str,
) -> dict[str, Mapping[str, object]] | str:
    """Read an entry's lockfile and find the packages it installed.

    Args:
        entry: The ``_npx`` entry.
        npm_entry: The entry's path as npm was given it, absolute.

    Returns:
        Lockfile entries by their path below the entry, for each installed
        package, or why the lockfile cannot be used.
    """
    try:
        lock = json.loads((entry / "package-lock.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "has no readable package-lock.json"
    packages = lock.get("packages") if isinstance(lock, dict) else None
    if not isinstance(packages, dict):
        return "has a package-lock.json without packages"
    installed: dict[str, Mapping[str, object]] = {}
    real_entry = os.path.realpath(entry)
    for locked, meta in packages.items():
        if locked == "":
            continue
        key = _entry_key(locked, npm_entry=npm_entry, real_entry=real_entry)
        if key is None or not isinstance(meta, dict) or meta.get("link"):
            return f"locks {locked!r}, which is not an installed registry package"
        try:
            mode = os.lstat(entry / key).st_mode
        except FileNotFoundError:
            if meta.get("optional") or meta.get("devOptional"):
                continue  # A platform-specific optional dependency npm skipped.
            return f"is missing {key}"
        if not stat.S_ISDIR(mode):
            return f"has {key} that is not a real directory"
        installed[key] = meta
    return installed


def entry_problem(
    entry: Path,
    cache: Path,
    *,
    npm_cache: Path | None = None,
    windows: bool | None = None,
) -> str | None:
    """Check one ``_npx`` entry's contents against its lockfile.

    Args:
        entry: ``<cache>/_npx/<hash>``.
        cache: The per-pin cache that holds it, already resolved.
        npm_cache: The cache path as npm was given it, if not *cache*.
        windows: Whether npm wrote Windows shims; defaults to ``os.name``.

    Returns:
        Why the entry cannot be trusted, or None when every installed file is
        the published one.
    """
    if windows is None:
        windows = os.name == "nt"
    try:
        npm_entry = os.path.abspath((npm_cache or cache) / "_npx" / entry.name)
        installed = _installed_packages(entry, npm_entry)
        if isinstance(installed, str):
            return installed
        for key, meta in installed.items():
            if meta.get("inBundle"):
                continue  # Its files come from the bundling package's tarball.
            reason = _package_problem(cache, entry / key, meta)
            if reason is not None:
                return f"{key} {reason}"
        return _tree_problem(entry, installed, windows=windows)
    except OSError as exc:
        return f"cannot be read ({exc})"


def verify_entry(
    entry: Path,
    cache: Path,
    *,
    npm_cache: Path | None = None,
) -> Fingerprint | str:
    """Verify an entry, or reuse a verification of the same fingerprint.

    Args:
        entry: ``<cache>/_npx/<hash>``.
        cache: The per-pin cache that holds it, already resolved.
        npm_cache: The cache path as npm was given it, if not *cache*.

    Returns:
        The verified fingerprint, or why the entry cannot be trusted.
    """
    key = (str(cache), entry.name)
    try:
        before = entry_fingerprint(entry)
    except OSError as exc:
        return f"cannot be read ({exc})"
    with _VERIFIED_LOCK:
        if _VERIFIED.get(key) == before:
            return before
    reason = entry_problem(entry, cache, npm_cache=npm_cache)
    if reason is not None:
        return reason
    try:
        after = entry_fingerprint(entry)
    except OSError as exc:
        return f"cannot be read ({exc})"
    if after != before:
        return "changed while lintro verified it"
    with _VERIFIED_LOCK:
        _VERIFIED[key] = after
    return after


def _refusal(entry: Path, reason: str) -> str:
    """Explain why lintro will not run or trust an entry.

    Args:
        entry: The ``_npx`` entry.
        reason: What lintro found.

    Returns:
        The message, naming the entry and how it is repaired.
    """
    return (
        f"lintro's npm cache entry {entry} {reason}, so its findings cannot be "
        "trusted; the next lintro run removes the entry and npx reinstalls the pin"
    )


def verified_cache(cache: Path) -> dict[str, Fingerprint]:
    """Verify every entry npx could run from a cache, before a spawn.

    Args:
        cache: The ``--cache`` the npx command carries.

    Returns:
        The verified fingerprint of each entry, by name.

    Raises:
        NpxIntegrityError: An entry does not match its lockfile.
    """
    real = Path(os.path.realpath(cache))
    fingerprints: dict[str, Fingerprint] = {}
    for entry in cache_entries(real):
        result = verify_entry(entry, real, npm_cache=cache)
        if isinstance(result, str):
            raise NpxIntegrityError(_refusal(entry, result))
        fingerprints[entry.name] = result
    return fingerprints


def check_unchanged(cache: Path, before: Mapping[str, Fingerprint]) -> None:
    """Require a cache to hold what it held before the spawn, after the run.

    Entries that existed before must be byte-for-byte the files verified,
    shown by an unchanged fingerprint. Entries that appeared during the run,
    npx's install of a cold pin, are verified now.

    Args:
        cache: The ``--cache`` the npx command carried.
        before: Fingerprints from :func:`verified_cache`.

    Raises:
        NpxIntegrityError: An entry changed during the run, or a new entry
            does not match its lockfile.
    """
    real = Path(os.path.realpath(cache))
    current = {entry.name: entry for entry in cache_entries(real)}
    for name, fingerprint in before.items():
        entry = real / "_npx" / name
        try:
            unchanged = entry_fingerprint(entry) == fingerprint
        except OSError:
            unchanged = False
        if not unchanged:
            raise NpxIntegrityError(
                _refusal(entry, "changed while the tool was running"),
            )
    for name, entry in current.items():
        if name in before:
            continue
        result = verify_entry(entry, real, npm_cache=cache)
        if isinstance(result, str):
            raise NpxIntegrityError(_refusal(entry, result))

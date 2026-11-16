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

- the package directory is a real directory, reached from the entry without
  any symlink (``node_modules``, scope directories and parent packages
  included);
- the tarball is in the cache and hashes to the lockfile's ``integrity``;
- every file pacote extracts from the tarball is installed with the same bytes,
  as a regular file with one link (a hard link could be rewritten through its
  other name later). pacote's own rules are followed: links are dropped and a
  packed ``.gitignore`` becomes ``.npmignore``. A declared executable may also
  have the CRLF on its ``#!`` line turned into LF, exactly as npm's bin-links
  ``fix-bin`` does (:func:`npm_fixed_bin`);
- the package directory holds no file, directory link or ``node_modules``
  the tarball lacks. Bundled packages are checked against the bundling
  package's tarball. Install scripts never run: every npx fallback carries
  ``--ignore-scripts`` (``NPX_ISOLATION_FLAGS`` in
  :mod:`lintro.tools.core.node_fallback`), so an install holds the published
  files and nothing else.

Each ``node_modules`` directory, at any depth, must be a real directory that
holds only lockfile packages, plus ``.package-lock.json`` at the top. Its
``.bin`` may hold only the executables those packages declare. On POSIX each one
must be a link to the declared file. On Windows each shim (``<name>``,
``<name>.cmd``, ``<name>.ps1``) must be byte for byte what npm's ``cmd-shim``
writes for that file (:func:`npm_cmd_shims`). The top-level ``.bin`` is on the
tool's ``PATH``, so an extra ``.bin/node`` would otherwise replace the ``node``
that the tool's ``#!/usr/bin/env node`` starts.

Node looks for a package it cannot find in the entry in every ``node_modules``
above it. No ``node_modules`` may exist between the entry and lintro's cache
root. When the lockfile lists an optional package npm skipped for this
platform, none may exist anywhere up to the filesystem root, nor in
``~/.node_modules`` or ``~/.node_libraries`` (:func:`ancestor_problem`).

**Binding the check to the run.** An entry's fingerprint is the type, inode,
size, mtime and ctime of everything in it. Users can set mtime but not ctime,
so on POSIX no file can be written, replaced or put back without changing it.
The fingerprint leaves out npm's ``concurrency.lock``, lintro's own lock asides,
and the times of the entry directory, all of which change when a lock is
taken. A fingerprint is taken before and after the content check, and the
check counts only when the two match. Verified fingerprints are remembered for
the life of the process, so an unchanged entry is not hashed twice.

Moving the entry, or ``_npx``, aside and back changes the mtime of the
directory holding it and the ctime of the moved directory, so the per-pin cache
directory and ``_npx`` are recorded too. A warm npx run changes neither (npm's
logs and update-check stamp are written in place, below them).

Before lintro spawns an npx fallback, it checks every entry in the command's
``--cache``. An entry whose fingerprint was already verified passes at once;
any other entry is verified again. An entry whose npm lock is held is waited
for, so a concurrent first install is checked once it is complete rather than
mistaken for a broken one. After the process exits, every entry and both
directories must be as they were before the spawn. An entry that appeared
during the run, which is npx's first install of the pin, is verified then. Any
failure raises :class:`NpxIntegrityError`, which lintro reports as an
execution failure with incomplete findings. It is never treated as a pass and
never produces findings.

**Limits.** The lockfile and the tarballs live in the same user-writable
cache as the install. This check catches a partial change, such as the one in
#3406 where ``bin.js`` was replaced and ``package.json`` left alone. It does
not stop a user who rewrites the lockfile, the tarball and the install so that
they agree. That user already controls lintro's cache, which lintro trusts as
it trusts its own installation, and likewise could move the directories above
the per-pin cache aside and back. ``NODE_PATH`` is not inspected. A cold run
executes npm's fresh install, which npm checks against the registry's
integrity hashes; lintro verifies it once the run has finished. On Windows,
``st_ctime`` is the creation time, so there the fingerprint relies on size,
mtime and inode.
"""

from __future__ import annotations

import base64
import binascii
import gzip
import hashlib
import io
import json
import os
import posixpath
import re
import stat
import tarfile
import threading
import time
import zlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

#: Prefix lintro gives entries and locks it has set aside before deletion.
STALE_PREFIX: str = ".lintro-stale-"

#: npm's per-entry lock directory, taken around every check or install.
NPM_LOCK_NAME: str = "concurrency.lock"

#: npm treats a lock whose mtime is older than this as abandoned.
_STALE_LOCK_SECONDS: float = 60.0

#: Longest wait for a live npm lock on an entry before checking it anyway.
_INSTALL_WAIT_SECONDS: float = 300.0

#: Integrity algorithms lintro checks, strongest first.
_ALGORITHMS: tuple[str, ...] = ("sha512", "sha384", "sha256", "sha1")

#: First bytes of a gzip stream; npm package tarballs are gzipped.
_GZIP_MAGIC: bytes = b"\x1f\x8b"

#: Suffixes of the Windows ``.bin`` shims npm writes beside the ``sh`` shim.
_SHIM_SUFFIXES: tuple[str, ...] = (".cmd", ".ps1")

#: Characters JavaScript's ``String.prototype.trim`` removes.
_JS_WHITESPACE: str = " \t\n\v\f\r         " "        　﻿"

#: ``cmd-shim``'s shebang pattern (lib/index.js), unchanged.
_SHEBANG = re.compile(
    r"^#!\s*(?:/usr/bin/env\s+(?:-S\s+)?((?:[^ \t=]+=[^ \t=]+\s+)*))?([^ \t]+)(.*)$",
)

#: bin-links ``fix-bin``: a ``#!`` line ending in CRLF.
_WINDOWS_HASHBANG = re.compile(r"^#![^\n]+\r\n")

#: ``cmd-shim``'s ``$VAR`` / ``${VAR}`` pattern in ``-S`` variables.
_DOLLAR = re.compile(r"\$\{?([^$@#?\- \t{}:]+)\}?")

#: ``cmd-shim``'s batch file preamble.
_CMD_HEAD: str = (
    "@ECHO off\r\nGOTO start\r\n:find_dp0\r\nSET dp0=%~dp0\r\nEXIT /b\r\n"
    ":start\r\nSETLOCAL\r\nCALL :find_dp0\r\n"
)

#: ``cmd-shim``'s ``sh`` shim preamble.
_SH_HEAD: str = (
    "#!/bin/sh\n"
    'basedir=$(dirname "$(echo "$0" | sed -e \'s,\\\\,/,g\')")\n'
    "\n"
    "case `uname` in\n"
    "    *CYGWIN*|*MINGW*|*MSYS*)\n"
    "        if command -v cygpath > /dev/null 2>&1; then\n"
    '            basedir=`cygpath -w "$basedir"`\n'
    "        fi\n"
    "    ;;\n"
    "esac\n"
    "\n"
)

#: ``cmd-shim``'s PowerShell shim preamble.
_PWSH_HEAD: str = (
    "#!/usr/bin/env pwsh\n"
    "$basedir=Split-Path $MyInvocation.MyCommand.Definition -Parent\n"
    "\n"
    '$exe=""\n'
    'if ($PSVersionTable.PSVersion -lt "6.0" -or $IsWindows) {\n'
    "  # Fix case when both the Windows and Linux builds of Node\n"
    "  # are installed in the same directory\n"
    '  $exe=".exe"\n'
    "}\n"
)

#: One path in an entry: relative path, file type, inode, size, mtime, ctime.
_Stat = tuple[str, int, int, int, int, int]

#: Everything in one ``_npx`` entry, sorted by path.
Fingerprint = tuple[_Stat, ...]

#: Fingerprints whose contents were verified, by (cache, entry name).
_VERIFIED: dict[tuple[str, str], Fingerprint] = {}
_VERIFIED_LOCK = threading.Lock()


class NpxIntegrityError(RuntimeError):
    """What npx would run from lintro's cache does not match its lockfile."""


@dataclass(frozen=True)
class CacheState:
    """What a cache held when lintro verified it, before a spawn.

    Attributes:
        entries: Verified fingerprint of each ``_npx`` entry, by name.
        directories: Rows for the per-pin cache directory and ``_npx``.
    """

    entries: Mapping[str, Fingerprint]
    directories: tuple[_Stat, ...]


@dataclass(frozen=True)
class _Lockfile:
    """The packages an entry's lockfile installed.

    Attributes:
        installed: Lockfile entries by their path below the entry.
        optional_missing: Whether an optional package was skipped.
    """

    installed: Mapping[str, Mapping[str, object]]
    optional_missing: bool


def forget_verified() -> None:
    """Drop every remembered verification, so the next check hashes again."""
    with _VERIFIED_LOCK:
        _VERIFIED.clear()


def _stat_row(path: str | Path, rel: str) -> _Stat:
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


def _directory_rows(cache: Path) -> tuple[_Stat, ...]:
    """Record the per-pin cache directory and ``_npx`` themselves.

    Args:
        cache: The per-pin cache, already resolved.

    Returns:
        A row for each of the two that exists.
    """
    rows: list[_Stat] = []
    for path, rel in ((cache, "."), (cache / "_npx", "_npx")):
        try:
            rows.append(_stat_row(path, rel))
        except FileNotFoundError:
            continue
    return tuple(rows)


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


# ---------------------------------------------------------------------------
# npm's own install-time rewrites: bin-links fix-bin and cmd-shim
# ---------------------------------------------------------------------------


def _js_trim(text: str) -> str:
    """Trim *text* as JavaScript's ``String.prototype.trim`` does.

    Args:
        text: Text to trim.

    Returns:
        The trimmed text.
    """
    return text.strip(_JS_WHITESPACE)


def npm_fixed_bin(data: bytes) -> bytes:
    """Return a declared executable as npm's bin-links ``fix-bin`` leaves it.

    ``fix-bin`` reads the first 2048 bytes; when they start a ``#!`` line that
    ends in CRLF, it rewrites the file as UTF-8 with that one CRLF turned into
    LF. Anything else is left alone.

    Args:
        data: The published file.

    Returns:
        The bytes npm installs for it.
    """
    if data[:2] != b"#!":
        return data
    head = data[:2048].decode("utf-8", errors="replace")
    if not _WINDOWS_HASHBANG.match(head):
        return data
    text = data.decode("utf-8", errors="replace")
    return re.sub(r"^(#![^\n]+)\r\n", r"\1\n", text, count=1).encode("utf-8")


def _percent_pairs(value: str) -> str:
    """Port ``cmd-shim``'s ``replaceDollarWithPercentPair``.

    Args:
        value: A variable's value.

    Returns:
        The value with ``$VAR`` and ``${VAR}`` written as ``%VAR%``.
    """
    return _DOLLAR.sub(lambda match: f"%{match.group(1)}%", value)


def _batch_variables(variables: str) -> str:
    """Port ``cmd-shim``'s ``convertToSetCommands``.

    Args:
        variables: ``NAME=value`` declarations from an ``env -S`` shebang.

    Returns:
        The batch ``@SET`` lines.
    """
    pairs: dict[str, str] = {}
    for declaration in variables.split(" "):
        parts = declaration.split("=")
        pairs[parts[0]] = parts[1] if len(parts) > 1 else ""
    lines = ""
    for key, value in pairs.items():
        name, text = _js_trim(key), _js_trim(value)
        if name and text:
            lines += f"@SET {name}={_percent_pairs(text)}\r\n"
    return lines


def _shebang(target_text: str) -> tuple[str, str, str] | None:
    """Parse a target's shebang the way ``cmd-shim`` does.

    Args:
        target_text: The executable's contents.

    Returns:
        The program, its arguments and ``env -S`` variables, or None.
    """
    first = re.split(r"\r*\n", _js_trim(target_text))[0]
    match = _SHEBANG.match(first)
    if match is None:
        return None
    return match.group(2), match.group(3) or "", match.group(1) or ""


def _unquote(text: str) -> str:
    """Drop one leading and one trailing double quote, as ``cmd-shim`` does.

    Args:
        text: Quoted program.

    Returns:
        The program without its outer quotes.
    """
    return re.sub(r'(^")|("$)', "", text)


def _direct_shims(rel_target: str) -> dict[str, str]:
    """Port ``cmd-shim``'s shims for a target without a shebang.

    Args:
        rel_target: Target relative to the ``.bin`` directory, POSIX form.

    Returns:
        Shim contents by suffix.
    """
    prog = '"%dp0%\\' + rel_target.replace("/", "\\") + '"'
    sh_prog = f'"$basedir/{rel_target}"'
    return {
        ".cmd": f"{_CMD_HEAD}{prog}   %*\r\n",
        "": f'{_SH_HEAD}exec {sh_prog}   "$@"\n',
        ".ps1": (
            f"{_PWSH_HEAD}# Support pipeline input\n"
            "if ($MyInvocation.ExpectingInput) {\n"
            f"  $input | & {sh_prog}   $args\n"
            "} else {\n"
            f"  & {sh_prog}   $args\n"
            "}\n"
            "exit $LASTEXITCODE\n"
        ),
    }


def npm_cmd_shims(rel_target: str, target_text: str) -> dict[str, str]:
    """Return the ``.bin`` shims npm's ``cmd-shim`` writes on Windows.

    A port of ``cmd-shim``'s ``writeShim_`` (7.0.0 in npm 10, 8.0.0 in npm 11;
    both generate the same text).

    Args:
        rel_target: Target relative to the ``.bin`` directory, POSIX form.
        target_text: The target's contents, decoded as UTF-8.

    Returns:
        Shim contents by suffix: ``.cmd``, ``.ps1`` and ``""`` (the sh shim).
    """
    shebang = _shebang(target_text)
    if shebang is None:
        return _direct_shims(rel_target)
    prog, args, variables = shebang
    args = _js_trim(args)
    sh_prog = prog.replace("\\", "/")
    long_prog = f'"%dp0%\\{prog}.exe"'
    sh_long_prog = _js_trim(f'"$basedir/{prog}"')
    pwsh_prog = f'"{sh_prog}$exe"'
    pwsh_long_prog = f'"$basedir/{prog}$exe"'
    target = '"%dp0%\\' + rel_target.replace("/", "\\") + '"'
    sh_target = f'"$basedir/{rel_target}"'
    cmd = (
        f"{_CMD_HEAD}{_batch_variables(variables)}\r\n"
        f"IF EXIST {long_prog} (\r\n"
        f'  SET "_prog={_unquote(long_prog)}"\r\n'
        ") ELSE (\r\n"
        f'  SET "_prog={_unquote(prog)}"\r\n'
        "  SET PATHEXT=%PATHEXT:;.JS;=;%\r\n"
        ")\r\n"
        "\r\n"
        "endLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & "
        f'"%_prog%" {args} {target} %*\r\n'
    )
    sh = (
        f"{_SH_HEAD}if [ -x {sh_long_prog} ]; then\n"
        f'  exec {variables}{sh_long_prog} {args} {sh_target} "$@"\n'
        "else \n"
        f'  exec {variables}{sh_prog} {args} {sh_target} "$@"\n'
        "fi\n"
    )
    pwsh = (
        f"{_PWSH_HEAD}$ret=0\n"
        f"if (Test-Path {pwsh_long_prog}) {{\n"
        "  # Support pipeline input\n"
        "  if ($MyInvocation.ExpectingInput) {\n"
        f"    $input | & {pwsh_long_prog} {args} {sh_target} $args\n"
        "  } else {\n"
        f"    & {pwsh_long_prog} {args} {sh_target} $args\n"
        "  }\n"
        "  $ret=$LASTEXITCODE\n"
        "} else {\n"
        "  # Support pipeline input\n"
        "  if ($MyInvocation.ExpectingInput) {\n"
        f"    $input | & {pwsh_prog} {args} {sh_target} $args\n"
        "  } else {\n"
        f"    & {pwsh_prog} {args} {sh_target} $args\n"
        "  }\n"
        "  $ret=$LASTEXITCODE\n"
        "}\n"
        "exit $ret\n"
    )
    return {".cmd": cmd, "": sh, ".ps1": pwsh}


# ---------------------------------------------------------------------------
# Package contents
# ---------------------------------------------------------------------------


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

    pacote drops the first path component and every link, ignores unsafe
    paths, and installs a ``.gitignore`` as ``.npmignore`` unless one came
    first. When a name repeats, the last member wins, as on extraction.

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


def _published(cache: Path, meta: Mapping[str, object]) -> dict[str, bytes] | str:
    """Read the files a package's checked tarball publishes.

    Args:
        cache: The per-pin cache.
        meta: The package's lockfile entry.

    Returns:
        The published files, or why they cannot be trusted.
    """
    data = _tarball(cache, meta.get("integrity"))
    if isinstance(data, str):
        return data
    return _published_files(data)


def _bin_path(target: str) -> str:
    """Normalise a ``bin`` target as npm-normalize-package-bin does.

    Args:
        target: The declared path.

    Returns:
        The path inside the package, without leading ``/`` or ``..``.
    """
    return posixpath.normpath(f"/{target}").lstrip("/")


def _bin_files(meta: Mapping[str, object]) -> set[str]:
    """Return the files a lockfile entry declares as executables.

    Args:
        meta: The package's lockfile entry.

    Returns:
        Paths inside the package.
    """
    declared = meta.get("bin")
    if not isinstance(declared, dict):
        return set()
    return {
        _bin_path(target) for target in declared.values() if isinstance(target, str)
    }


def _installed_matches(
    directory: Path,
    files: Mapping[str, bytes],
    bins: set[str],
) -> str | None:
    """Compare installed files with the published ones.

    Args:
        directory: The installed package directory.
        files: Published contents by relative path.
        bins: Declared executables, which npm may have passed through
            :func:`npm_fixed_bin`.

    Returns:
        Why the install differs, or None.
    """
    for rel, published in files.items():
        path = directory / rel
        try:
            info = os.lstat(path)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                return f"has {rel} installed as something other than its own file"
            installed = path.read_bytes()
        except OSError:
            return f"is missing {rel}"
        if installed != published and not (
            rel in bins and installed == npm_fixed_bin(published)
        ):
            return f"has {rel} changed from the published package"
    return None


def _unpublished_file(directory: Path, files: Mapping[str, bytes]) -> str | None:
    """Find a path in a package that its tarball does not hold.

    The package's own top-level ``node_modules`` holds other lockfile
    packages, checked by :func:`_tree_problem`; even so it must not be a link.

    Args:
        directory: The installed package directory.
        files: Published contents by relative path.

    Returns:
        The first unexpected path, or None.
    """
    for root, dirs, names in os.walk(directory):
        rel_root = Path(root).relative_to(directory).as_posix()
        prefix = "" if rel_root == "." else f"{rel_root}/"
        for name in dirs:
            if os.path.islink(os.path.join(root, name)):
                return f"{prefix}{name}"
        if not prefix and "node_modules" in dirs:
            dirs.remove("node_modules")
        for name in names:
            if f"{prefix}{name}" not in files:
                return f"{prefix}{name}"
    return None


def _bundler(key: str, installed: Mapping[str, Mapping[str, object]]) -> str | None:
    """Find the installed package whose tarball carries a bundled package.

    Args:
        key: The bundled package's key.
        installed: Installed lockfile entries by key.

    Returns:
        The nearest enclosing key that is not bundled itself, or None.
    """
    parent = key
    while "/node_modules/" in parent:
        parent = parent.rsplit("/node_modules/", 1)[0]
        if parent in installed and not installed[parent].get("inBundle"):
            return parent
    return None


def _bundled_problem(
    entry: Path,
    key: str,
    installed: Mapping[str, Mapping[str, object]],
    published: Mapping[str, Mapping[str, bytes]],
) -> str | None:
    """Check a bundled package against the tarball that carries it.

    Its files were compared with the bundling package's; this finds anything
    in its directory that tarball does not hold.

    Args:
        entry: The ``_npx`` entry.
        key: The bundled package's key.
        installed: Installed lockfile entries by key.
        published: Published files of each non-bundled package, by key.

    Returns:
        Why the bundled package cannot be trusted, or None.
    """
    bundler = _bundler(key, installed)
    if bundler is None:
        return f"{key} is bundled by no installed package"
    inner = f"{key[len(bundler) + 1 :]}/"
    files = {
        rel[len(inner) :]: data
        for rel, data in published[bundler].items()
        if rel.startswith(inner)
    }
    extra = _unpublished_file(entry / key, files)
    if extra is not None:
        return f"{key} has {extra}, which {bundler} does not publish"
    return None


def _packages_problem(
    entry: Path,
    cache: Path,
    installed: Mapping[str, Mapping[str, object]],
) -> str | None:
    """Check every installed package against its published files.

    Args:
        entry: The ``_npx`` entry.
        cache: The per-pin cache.
        installed: Installed lockfile entries by key.

    Returns:
        Why a package cannot be trusted, or None.
    """
    published: dict[str, dict[str, bytes]] = {}
    bundled = [key for key, meta in installed.items() if meta.get("inBundle")]
    for key, meta in installed.items():
        if key in bundled:
            continue
        files = _published(cache, meta)
        if isinstance(files, str):
            return f"{key} {files}"
        published[key] = files
        reason = _installed_matches(entry / key, files, _bin_files(meta))
        if reason is None:
            extra = _unpublished_file(entry / key, files)
            if extra is not None:
                reason = f"has {extra}, which is not in the published package"
        if reason is not None:
            return f"{key} {reason}"
    for key in bundled:
        reason = _bundled_problem(entry, key, installed, published)
        if reason is not None:
            return reason
    return None


# ---------------------------------------------------------------------------
# node_modules trees and .bin
# ---------------------------------------------------------------------------


def _declared_bins(
    entry: Path,
    prefix: str,
    installed: Mapping[str, Mapping[str, object]],
) -> dict[str, set[Path]]:
    """Map the executables the packages in one ``node_modules`` declare.

    Args:
        entry: The ``_npx`` entry, a real directory.
        prefix: Lockfile key prefix of the directory, such as
            ``node_modules/``.
        installed: Installed lockfile entries by key.

    Returns:
        Executable name to the files it may run.
    """
    bins: dict[str, set[Path]] = {}
    for key, meta in installed.items():
        rest = key[len(prefix) :] if key.startswith(prefix) else None
        if rest is None or "/node_modules/" in f"/{rest}/":
            continue
        declared = meta.get("bin")
        if not isinstance(declared, dict):
            continue
        for name, target in declared.items():
            if isinstance(target, str):
                bins.setdefault(str(name), set()).add(entry / key / _bin_path(target))
    return bins


def _shim_matches(child: Path, suffix: str, targets: set[Path]) -> bool:
    """Report whether a Windows shim is exactly what npm writes for a target.

    Args:
        child: The shim in ``.bin``.
        suffix: ``.cmd``, ``.ps1`` or ``""``.
        targets: Files the shim's name may run.

    Returns:
        True when the shim is a regular file matching one target's shim.
    """
    info = os.lstat(child)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        return False
    content = child.read_bytes()
    for target in targets:
        rel = Path(os.path.relpath(target, child.parent)).as_posix()
        text = target.read_bytes().decode("utf-8", errors="replace")
        if npm_cmd_shims(rel, text)[suffix].encode("utf-8") == content:
            return True
    return False


def _bin_problem(
    bin_dir: Path,
    bins: Mapping[str, set[Path]],
    *,
    windows: bool,
) -> str | None:
    """Check that a ``.bin`` directory holds only declared executables.

    Args:
        bin_dir: ``<node_modules>/.bin``.
        bins: Declared executable names and the files they run.
        windows: Whether npm wrote Windows shims rather than links.

    Returns:
        Why the directory cannot be trusted, or None.
    """
    if bin_dir.is_symlink() or not bin_dir.is_dir():
        return f"has {bin_dir.name} that is not a directory"
    for child in bin_dir.iterdir():
        name, suffix = child.name, ""
        if windows:
            for candidate in _SHIM_SUFFIXES:
                if name.endswith(candidate):
                    name, suffix = name.removesuffix(candidate), candidate
        if name not in bins:
            return f"has .bin/{child.name}, which no locked package declares"
        if windows:
            if not _shim_matches(child, suffix, bins[name]):
                return f"has .bin/{child.name}, which is not the shim npm writes"
            continue
        real = {os.path.realpath(target) for target in bins[name]}
        if not child.is_symlink() or os.path.realpath(child) not in real:
            return f"has .bin/{child.name} pointing outside the package declaring it"
    return None


def _modules_child_problem(
    child: Path,
    prefix: str,
    installed: Mapping[str, Mapping[str, object]],
) -> str | None:
    """Check one child of a ``node_modules`` directory other than ``.bin``.

    Args:
        child: The child.
        prefix: Lockfile key prefix of its directory.
        installed: Installed lockfile entries by key.

    Returns:
        Why the child cannot be trusted, or None.
    """
    if child.name == ".package-lock.json" and prefix == "node_modules/":
        return None
    if not child.name.startswith("@"):
        if f"{prefix}{child.name}" not in installed:
            return f"has {prefix}{child.name}, not in its lockfile"
        return None
    if child.is_symlink() or not child.is_dir():
        return f"has {prefix}{child.name} that is not a real directory"
    for scoped in child.iterdir():
        key = f"{prefix}{child.name}/{scoped.name}"
        if key not in installed:
            return f"has {key}, not in its lockfile"
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
        if os.path.lexists(entry / key / "node_modules")
    ]
    for prefix in prefixes:
        directory = entry / prefix
        if not os.path.lexists(directory):
            continue
        if directory.is_symlink() or not directory.is_dir():
            return f"has {prefix.rstrip('/')} that is not a real directory"
        for child in directory.iterdir():
            if child.name == ".bin":
                reason = _bin_problem(
                    child,
                    _declared_bins(entry, prefix, installed),
                    windows=windows,
                )
            else:
                reason = _modules_child_problem(child, prefix, installed)
            if reason is not None:
                return reason
    return None


# ---------------------------------------------------------------------------
# Lockfile
# ---------------------------------------------------------------------------


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


def _installed_package(entry: Path, key: str, meta: Mapping[str, object]) -> str | None:
    """Check that a locked package is installed as a real, unlinked directory.

    Args:
        entry: The ``_npx`` entry.
        key: The package's path below the entry.
        meta: Its lockfile entry.

    Returns:
        ``""`` for an optional package npm skipped, why the package cannot be
        trusted, or None when it is installed in place.
    """
    try:
        mode = os.lstat(entry / key).st_mode
    except FileNotFoundError:
        if meta.get("optional") or meta.get("devOptional"):
            return ""  # A platform package npm skipped.
        return f"is missing {key}"
    if not stat.S_ISDIR(mode):
        return f"has {key} that is not a real directory"
    real = os.path.join(os.path.realpath(entry), *key.split("/"))
    if os.path.realpath(entry / key) != real:
        return f"reaches {key} through a symlink"
    return None


def _read_lockfile(entry: Path, npm_entry: str) -> _Lockfile | str:
    """Read an entry's lockfile and find the packages it installed.

    Args:
        entry: The ``_npx`` entry.
        npm_entry: The entry's path as npm was given it, absolute.

    Returns:
        The installed packages, or why the lockfile cannot be used.
    """
    try:
        lock = json.loads((entry / "package-lock.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "has no readable package-lock.json"
    packages = lock.get("packages") if isinstance(lock, dict) else None
    if not isinstance(packages, dict):
        return "has a package-lock.json without packages"
    installed: dict[str, Mapping[str, object]] = {}
    optional_missing = False
    real_entry = os.path.realpath(entry)
    for locked, meta in packages.items():
        if locked == "":
            continue
        key = _entry_key(locked, npm_entry=npm_entry, real_entry=real_entry)
        if key is None or not isinstance(meta, dict) or meta.get("link"):
            return f"locks {locked!r}, which is not an installed registry package"
        reason = _installed_package(entry, key, meta)
        if reason == "":
            optional_missing = True
        elif reason is not None:
            return reason
        else:
            installed[key] = meta
    return _Lockfile(installed=installed, optional_missing=optional_missing)


def _npm_entry(entry: Path, cache: Path, npm_cache: Path | None) -> str:
    """Return the entry's path as npm was given it.

    Args:
        entry: The ``_npx`` entry.
        cache: The per-pin cache that holds it, already resolved.
        npm_cache: The cache path as npm was given it, if not *cache*.

    Returns:
        The absolute, unresolved entry path.
    """
    return os.path.abspath((npm_cache or cache) / "_npx" / entry.name)


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
        if not stat.S_ISDIR(os.lstat(entry).st_mode):
            return "is not a real directory"
        lockfile = _read_lockfile(entry, _npm_entry(entry, cache, npm_cache))
        if isinstance(lockfile, str):
            return lockfile
        return _packages_problem(entry, cache, lockfile.installed) or _tree_problem(
            entry,
            lockfile.installed,
            windows=windows,
        )
    except OSError as exc:
        return f"cannot be read ({exc})"


def ancestor_problem(
    entry: Path,
    cache: Path,
    *,
    npm_cache: Path | None = None,
) -> str | None:
    """Refuse a ``node_modules`` above the entry that Node could load from.

    Below lintro's cache root none may exist. When the lockfile lists an
    optional package npm skipped, Node would search every ``node_modules`` up
    to the filesystem root and the ``$HOME`` global folders for it, so none
    may exist there either.

    Args:
        entry: ``<cache>/_npx/<hash>``.
        cache: The per-pin cache that holds it, already resolved.
        npm_cache: The cache path as npm was given it, if not *cache*.

    Returns:
        Why the entry's surroundings cannot be trusted, or None.
    """
    from lintro.utils import file_cache

    lockfile = _read_lockfile(entry, _npm_entry(entry, cache, npm_cache))
    anywhere = isinstance(lockfile, str) or lockfile.optional_missing
    root = Path(os.path.realpath(file_cache.CACHE_DIR))
    real_cache = Path(os.path.realpath(cache))
    owned = root if real_cache == root or root in real_cache.parents else real_cache
    candidates = [
        directory / "node_modules"
        for directory in Path(os.path.realpath(entry)).parents
        if anywhere or directory == owned or owned in directory.parents
    ]
    if anywhere:
        home = Path.home()
        candidates += [home / ".node_modules", home / ".node_libraries"]
    for candidate in candidates:
        if os.path.lexists(candidate):
            return (
                f"would let Node load packages from {candidate}, outside the "
                "entry; remove it"
            )
    return None


# ---------------------------------------------------------------------------
# Verification bound to a spawn
# ---------------------------------------------------------------------------


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


def _await_npm(entry: Path) -> None:
    """Wait while npm holds an entry's lock, so a running install can finish.

    A lock untouched for npm's stale threshold, or held past the wait, is
    not waited for further; the entry is then checked as it is, and an
    unfinished install fails that check.

    Args:
        entry: The ``_npx`` entry.
    """
    lock = entry / NPM_LOCK_NAME
    deadline = time.monotonic() + _INSTALL_WAIT_SECONDS
    while time.monotonic() < deadline:
        try:
            touched = os.lstat(lock).st_mtime
        except FileNotFoundError:
            return
        if time.time() - touched > _STALE_LOCK_SECONDS:
            return
        time.sleep(0.1)


def _checked(entry: Path, cache: Path, npm_cache: Path) -> Fingerprint:
    """Wait for npm, then verify an entry and its surroundings, or refuse.

    Args:
        entry: The ``_npx`` entry.
        cache: The per-pin cache, already resolved.
        npm_cache: The cache path as npm is given it.

    Returns:
        The entry's verified fingerprint.

    Raises:
        NpxIntegrityError: The entry or its surroundings cannot be trusted.
    """
    _await_npm(entry)
    result = verify_entry(entry, cache, npm_cache=npm_cache)
    if isinstance(result, str):
        raise NpxIntegrityError(_refusal(entry, result))
    reason = ancestor_problem(entry, cache, npm_cache=npm_cache)
    if reason is not None:
        raise NpxIntegrityError(_refusal(entry, reason))
    return result


def verified_cache(cache: Path) -> CacheState:
    """Verify every entry npx could run from a cache, before a spawn.

    Args:
        cache: The ``--cache`` the npx command carries.

    Returns:
        What the cache held: each entry's verified fingerprint and the rows
        of the cache directory and ``_npx``.

    Raises:
        NpxIntegrityError: An entry does not match its lockfile, or the
            cache was moved or replaced during the check.
    """
    real = Path(os.path.realpath(cache))
    # A concurrent first install also writes beside ``_npx`` (``_cacache``,
    # ``_logs``): let it finish before recording the directories.
    for entry in cache_entries(real):
        _await_npm(entry)
    directories = _directory_rows(real)
    entries = {
        entry.name: _checked(entry, real, cache) for entry in cache_entries(real)
    }
    if entries and _directory_rows(real) != directories:
        raise NpxIntegrityError(
            f"lintro's npm cache {real} changed while lintro verified it",
        )
    return CacheState(entries=entries, directories=directories)


def check_unchanged(cache: Path, before: CacheState) -> None:
    """Require a cache to hold what it held before the spawn, after the run.

    Entries that existed before must be byte-for-byte the files verified,
    shown by an unchanged fingerprint, and neither the cache directory nor
    ``_npx`` may have been moved or had entries moved in or out. Entries that
    appeared during the run, npx's install of a cold pin, are verified now.

    Args:
        cache: The ``--cache`` the npx command carried.
        before: What :func:`verified_cache` found.

    Raises:
        NpxIntegrityError: An entry or the cache changed during the run, or a
            new entry does not match its lockfile.
    """
    real = Path(os.path.realpath(cache))
    if before.entries and _directory_rows(real) != before.directories:
        raise NpxIntegrityError(
            f"lintro's npm cache {real} had entries moved or replaced while "
            "the tool was running, so its findings cannot be trusted",
        )
    for name, fingerprint in before.entries.items():
        entry = real / "_npx" / name
        try:
            unchanged = entry_fingerprint(entry) == fingerprint
        except OSError:
            unchanged = False
        if not unchanged:
            raise NpxIntegrityError(
                _refusal(entry, "changed while the tool was running"),
            )
    for entry in cache_entries(real):
        if entry.name not in before.entries:
            _checked(entry, real, cache)
            continue
        reason = ancestor_problem(entry, real, npm_cache=cache)
        if reason is not None:
            raise NpxIntegrityError(_refusal(entry, reason))

"""Build ``_npx`` cache entries on disk the way npm lays them out (#3406).

Each package is packed into a real gzipped tarball under a ``package/`` root,
its sha512 integrity is computed here, the tarball is stored where npm's
content cache keeps it (``<cache>/_cacache/content-v2/sha512/..``), and the
files are installed under ``<entry>/<key>``. The entry's
``package-lock.json`` records each key's integrity and declared executables,
and ``node_modules/.bin`` holds a link for each declared executable at the
top level. Nothing is downloaded.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import tarfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class LockedPackage:
    """One package an entry installs.

    Attributes:
        key: Lockfile key, such as ``node_modules/prettier``.
        files: File contents by path inside the package.
        bins: Executable name to the file it runs.
        extra: Further lockfile fields, such as ``{"optional": True}``.
        installed: Whether the files are installed at all.
    """

    key: str
    files: Mapping[str, str]
    bins: Mapping[str, str] = field(default_factory=dict)
    extra: Mapping[str, object] = field(default_factory=dict)
    installed: bool = True


def package_tarball(files: Mapping[str, str]) -> bytes:
    """Pack files the way ``npm pack`` does, under a ``package/`` root.

    Args:
        files: File contents by path inside the package.

    Returns:
        The gzipped tarball.
    """
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for rel, text in files.items():
            data = text.encode("utf-8")
            member = tarfile.TarInfo(f"package/{rel}")
            member.size = len(data)
            member.mode = 0o644
            archive.addfile(member, io.BytesIO(data))
    return buffer.getvalue()


def sri_sha512(data: bytes) -> str:
    """Compute a Subresource Integrity string as npm records one.

    Args:
        data: Tarball bytes.

    Returns:
        ``sha512-<base64 digest>``.
    """
    return "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode()


def cached_tarball_path(cache: Path, integrity: str) -> Path:
    """Locate where npm's content cache keeps a tarball.

    Args:
        cache: The per-pin npm cache.
        integrity: ``sha512-<base64>``.

    Returns:
        ``<cache>/_cacache/content-v2/sha512/<hex[:2]>/<hex[2:4]>/<hex[4:]>``.
    """
    algorithm, _, encoded = integrity.partition("-")
    hexdigest = base64.b64decode(encoded).hex()
    return (
        cache
        / "_cacache"
        / "content-v2"
        / algorithm
        / hexdigest[:2]
        / hexdigest[2:4]
        / hexdigest[4:]
    )


def tool_package(
    package: str = "prettier",
    version: str = "3.9.4",
    *,
    binary: str = "prettier",
    manifest_name: str | None = None,
) -> LockedPackage:
    """Describe a tool package with one executable.

    Args:
        package: npm package name.
        version: Version in its ``package.json``.
        binary: Executable name.
        manifest_name: Name written into ``package.json``; defaults to
            *package*.

    Returns:
        The package, at the top of ``node_modules``.
    """
    manifest = {
        "name": manifest_name or package,
        "version": version,
        "bin": {binary: "bin.js"},
    }
    return LockedPackage(
        key=f"node_modules/{package}",
        files={
            "package.json": json.dumps(manifest),
            "bin.js": "#!/usr/bin/env node\nrequire('./lib/index.js');\n",
            "lib/index.js": "module.exports = 1;\n",
        },
        bins={binary: "bin.js"},
    )


def write_npx_entry(
    cache: Path,
    packages: Sequence[LockedPackage],
    *,
    name: str = "0123456789abcdef",
    lock_prefix: str = "",
) -> Path:
    """Write one ``_npx`` entry, its lockfile and its cached tarballs.

    Args:
        cache: The per-pin npm cache.
        packages: Packages the entry installs.
        name: Entry (hash) directory name.
        lock_prefix: Prefix written before every lockfile key, as npm does
            when the cache path runs through a symlink.

    Returns:
        The entry directory.
    """
    entry = cache / "_npx" / name
    entry.mkdir(parents=True, exist_ok=True)
    locked: dict[str, object] = {"": {"dependencies": {}}}
    for package in packages:
        data = package_tarball(package.files)
        integrity = sri_sha512(data)
        tarball = cached_tarball_path(cache, integrity)
        tarball.parent.mkdir(parents=True, exist_ok=True)
        tarball.write_bytes(data)
        meta: dict[str, object] = {"version": "0.0.0", "integrity": integrity}
        if package.bins:
            meta["bin"] = dict(package.bins)
        meta.update(package.extra)
        locked[f"{lock_prefix}{package.key}"] = meta
        if not package.installed:
            continue
        directory = entry / package.key
        for rel, text in package.files.items():
            path = directory / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        top = package.key.count("/node_modules/") == 0
        for binary, target in package.bins.items() if top else ():
            bin_dir = entry / "node_modules" / ".bin"
            bin_dir.mkdir(parents=True, exist_ok=True)
            relative = package.key.removeprefix("node_modules/")
            (bin_dir / binary).symlink_to(f"../{relative}/{target}")
    (entry / "package-lock.json").write_text(
        json.dumps({"name": name, "lockfileVersion": 3, "packages": locked}),
        encoding="utf-8",
    )
    return entry

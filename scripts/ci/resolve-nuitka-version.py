#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""Resolve the locked Nuitka version for the binary build cache key.

The Nuitka compile cache persisted by ``build-binary.yml`` (#2484) is only
valid for the Nuitka release that produced it: a Nuitka bump changes the
generated C sources wholesale, so every ccache entry from the previous
version is dead weight. Keying the cache on the locked version makes that
invalidation automatic — a new version simply starts from a cold cache
instead of restoring a useless one.

``uv.lock`` is the single source of truth for the version actually installed
by ``uv sync --group build``; ``pyproject.toml`` only carries the ``>=``
specifier, which does not identify a build.

Usage:
    python3 scripts/ci/resolve-nuitka-version.py [--lockfile PATH]
                                                 [--package NAME]

Behavior:
    - Prints ``nuitka-version=<version>`` to stdout.
    - When ``GITHUB_OUTPUT`` is set, appends the same line so jobs can read
      ``steps.<id>.outputs.nuitka-version``.

Exit codes:
    0 — Version resolved.
    1 — Lockfile missing, unparseable, or without the requested package.
"""

from __future__ import annotations

import argparse
import os
import sys
import tomllib
from pathlib import Path

DEFAULT_PACKAGE = "nuitka"
DEFAULT_LOCKFILE = Path("uv.lock")

EXIT_OK = 0
EXIT_ERROR = 1


def resolve_locked_version(*, lockfile: Path, package: str) -> str:
    """Return the version ``uv.lock`` pins for ``package``.

    Args:
        lockfile: Path to the ``uv.lock`` file to read.
        package: Distribution name to look up, matched case-insensitively.

    Returns:
        The locked version string.

    Raises:
        FileNotFoundError: If ``lockfile`` does not exist.
        ValueError: If the lockfile is unparseable or pins no such package.
    """
    if not lockfile.is_file():
        msg = f"lockfile not found: {lockfile}"
        raise FileNotFoundError(msg)
    try:
        data = tomllib.loads(lockfile.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        msg = f"lockfile is not valid TOML: {lockfile}"
        raise ValueError(msg) from exc
    wanted = package.casefold()
    for entry in data.get("package", []):
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if isinstance(name, str) and name.casefold() == wanted:
            version = entry.get("version")
            if isinstance(version, str) and version:
                return version
            msg = f"package {package!r} in {lockfile} has no version"
            raise ValueError(msg)
    msg = f"package {package!r} not found in {lockfile}"
    raise ValueError(msg)


def _write_output(*, version: str) -> None:
    """Emit the resolved version to stdout and to ``GITHUB_OUTPUT`` when set.

    Args:
        version: The locked version string to emit.
    """
    line = f"nuitka-version={version}"
    print(line)
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with Path(github_output).open("a", encoding="utf-8") as handle:
            handle.write(f"{line}\n")


def main(argv: list[str] | None = None) -> int:
    """Resolve the locked Nuitka version and emit it as a step output.

    Args:
        argv: Command-line arguments, defaulting to ``sys.argv[1:]``.

    Returns:
        ``0`` on success, ``1`` when the version cannot be resolved.
    """
    parser = argparse.ArgumentParser(
        description="Resolve the locked Nuitka version for the build cache key.",
    )
    parser.add_argument(
        "--lockfile",
        type=Path,
        default=DEFAULT_LOCKFILE,
        help="path to uv.lock (default: %(default)s)",
    )
    parser.add_argument(
        "--package",
        default=DEFAULT_PACKAGE,
        help="package name to resolve (default: %(default)s)",
    )
    args = parser.parse_args(argv)
    try:
        version = resolve_locked_version(
            lockfile=args.lockfile,
            package=args.package,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    _write_output(version=version)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Bump the ``lintro`` dependency pin in the lintro-pre-commit mirror.

The mirror repository (``lgtm-hq/lintro-pre-commit``) carries a single
``pyproject.toml`` whose only job is to pin the published ``lintro`` wheel, for
example ``lintro==0.69.0``. On each py-lintro release, CI rewrites that pin to
the freshly released version so pre-commit installs the matching wheel.

Two modes are supported:

* Write mode (default): rewrite the pin to the requested version.
* Check mode (``--check``): verify the pin already matches, exiting non-zero on
  drift.

Usage:
    python scripts/ci/mirror/bump_pin.py --pyproject PATH --version 1.2.3
    python scripts/ci/mirror/bump_pin.py --pyproject PATH --version 1.2.3 --check
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path


def _read(*, path: Path) -> str:
    """Return the text content of ``path``.

    Args:
        path: File to read.

    Returns:
        The file's UTF-8 text.
    """
    return path.read_text(encoding="utf-8")


def _find_lintro_requirement(*, data: dict[str, object]) -> str:
    """Return the ``lintro==`` requirement from parsed ``pyproject.toml`` data.

    Args:
        data: Parsed TOML document.

    Returns:
        The exact requirement string (for example ``lintro==0.69.0``).

    Raises:
        ValueError: If no ``lintro==`` dependency is present.
    """
    project = data.get("project")
    if not isinstance(project, dict):
        msg = "No [project] table found in pyproject.toml"
        raise ValueError(msg)

    matches: list[str] = []
    dependencies = project.get("dependencies", [])
    if isinstance(dependencies, list):
        matches.extend(
            dep
            for dep in dependencies
            if isinstance(dep, str) and dep.startswith("lintro==")
        )

    optional = project.get("optional-dependencies", {})
    if isinstance(optional, dict):
        for group_deps in optional.values():
            if not isinstance(group_deps, list):
                continue
            matches.extend(
                dep
                for dep in group_deps
                if isinstance(dep, str) and dep.startswith("lintro==")
            )

    if not matches:
        msg = "No 'lintro==<version>' pin found in pyproject.toml dependencies"
        raise ValueError(msg)
    if len(matches) != 1:
        msg = (
            "Expected exactly one 'lintro==<version>' pin, "
            f"found {len(matches)} in dependencies"
        )
        raise ValueError(msg)
    return matches[0]


def _current_pin(*, content: str) -> str:
    """Return the currently pinned lintro version.

    Args:
        content: The ``pyproject.toml`` text.

    Returns:
        The pinned version string.

    Raises:
        ValueError: If no ``lintro==`` pin is present.
    """
    data = tomllib.loads(content)
    requirement = _find_lintro_requirement(data=data)
    return requirement.split("==", 1)[1]


def bump(*, path: Path, version: str) -> bool:
    """Rewrite the lintro pin in ``path`` to ``version``.

    Args:
        path: Path to the mirror ``pyproject.toml``.
        version: Target lintro version (no leading ``v``).

    Returns:
        True if the file was modified, False if it already matched.
    """
    content = _read(path=path)
    data = tomllib.loads(content)
    requirement = _find_lintro_requirement(data=data)
    current = requirement.split("==", 1)[1]
    if current == version:
        return False

    new_requirement = f"lintro=={version}"
    if content.count(requirement) != 1:
        msg = (
            "Expected exactly one occurrence of the pinned requirement "
            f"{requirement!r} in pyproject.toml"
        )
        raise ValueError(msg)

    updated = content.replace(requirement, new_requirement, 1)
    path.write_text(updated, encoding="utf-8")
    return True


def _parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        argv: Argument vector excluding the program name.

    Returns:
        The parsed namespace.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pyproject",
        required=True,
        type=Path,
        help="Path to the mirror's pyproject.toml.",
    )
    parser.add_argument(
        "--version",
        required=True,
        help="Target lintro version (without a leading 'v').",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify the pin already matches; exit non-zero on drift.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point.

    Args:
        argv: Optional argument vector for testing.

    Returns:
        Process exit code.
    """
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    version = args.version.lstrip("v")
    content = _read(path=args.pyproject)
    current = _current_pin(content=content)

    if args.check:
        if current == version:
            print(f"OK: lintro pin already at {version}")
            return 0
        print(
            f"Drift: lintro pin is {current}, expected {version}",
            file=sys.stderr,
        )
        return 1

    if bump(path=args.pyproject, version=version):
        print(f"Bumped lintro pin: {current} -> {version}")
    else:
        print(f"No change: lintro pin already at {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

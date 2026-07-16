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
import re
import sys
import tomllib
from pathlib import Path

# Match the ``lintro==<version>`` requirement inside a single dependency string
# (already extracted from the parsed TOML, so file-level comments or unrelated
# strings can never be matched here).
_REQUIREMENT_RE = re.compile(r"^lintro==(?P<version>.+)$")


def _read(*, path: Path) -> str:
    """Return the text content of ``path``.

    Args:
        path: File to read.

    Returns:
        The file's UTF-8 text.
    """
    return path.read_text(encoding="utf-8")


def _iter_dependency_strings(data: dict[str, object]) -> list[str]:
    """Return every dependency requirement string declared in ``pyproject``.

    Both ``[project].dependencies`` and ``[project.optional-dependencies]`` are
    inspected so the real pin is located regardless of where it is declared.

    Args:
        data: The parsed ``pyproject.toml`` mapping.

    Returns:
        list[str]: All requirement strings found under ``[project]``.
    """
    project = data.get("project", {})
    if not isinstance(project, dict):
        return []
    requirements: list[str] = []
    core = project.get("dependencies", [])
    if isinstance(core, list):
        requirements.extend(str(item) for item in core)
    optional = project.get("optional-dependencies", {})
    if isinstance(optional, dict):
        for group in optional.values():
            if isinstance(group, list):
                requirements.extend(str(item) for item in group)
    return requirements


def _current_pin(*, content: str) -> str:
    """Return the currently pinned lintro version from the dependency table.

    The version is read from the parsed ``[project]`` dependency requirements
    (not a file-wide text scan), so a comment or unrelated string containing
    ``lintro==`` cannot be mistaken for the real pin.

    Args:
        content: The ``pyproject.toml`` text.

    Returns:
        The pinned version string.

    Raises:
        ValueError: If no ``lintro==`` dependency pin is present.
    """
    data = tomllib.loads(content)
    for requirement in _iter_dependency_strings(data):
        match = _REQUIREMENT_RE.match(requirement.strip())
        if match is not None:
            return match.group("version")
    msg = "No 'lintro==<version>' dependency pin found in pyproject.toml"
    raise ValueError(msg)


def bump(*, path: Path, version: str) -> bool:
    """Rewrite the lintro pin in ``path`` to ``version``.

    The authoritative current version is read from the parsed dependency table,
    then that exact ``lintro==<current>`` requirement literal is rewritten in
    the file text so the quote style and surrounding whitespace are preserved
    and no unrelated ``lintro==`` occurrence is touched.

    Args:
        path: Path to the mirror ``pyproject.toml``.
        version: Target lintro version (no leading ``v``).

    Returns:
        True if the file was modified, False if it already matched.
    """
    content = _read(path=path)
    current = _current_pin(content=content)
    if current == version:
        return False
    pin_re = re.compile(
        r"(?P<prefix>[\"']lintro==)" + re.escape(current) + r"(?P<suffix>[\"'])",
    )
    updated, count = pin_re.subn(
        lambda m: f"{m.group('prefix')}{version}{m.group('suffix')}",
        content,
    )
    if count == 0:
        msg = f"Could not rewrite 'lintro=={current}' pin in {path}"
        raise ValueError(msg)
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

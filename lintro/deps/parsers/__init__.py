"""Dependency manifest parsers.

Each parser turns a manifest file into a list of
:class:`~lintro.deps.models.Dependency` objects with classified version specs.
:func:`parse_file` dispatches to the correct parser by file name.
"""

from __future__ import annotations

from pathlib import Path

from lintro.deps.models import Dependency
from lintro.deps.parsers.cargo_parser import CargoParser
from lintro.deps.parsers.package_json_parser import PackageJsonParser
from lintro.deps.parsers.pyproject_parser import PyprojectParser
from lintro.deps.parsers.requirements_parser import RequirementsParser

__all__ = [
    "CargoParser",
    "PackageJsonParser",
    "PyprojectParser",
    "RequirementsParser",
    "SUPPORTED_FILENAMES",
    "is_supported_manifest",
    "parse_file",
]

# Files the validator knows how to parse, in discovery order.
SUPPORTED_FILENAMES: tuple[str, ...] = (
    "pyproject.toml",
    "requirements.txt",
    "package.json",
    "Cargo.toml",
)

_SUPPORTED_NAME_SET: frozenset[str] = frozenset(
    name.lower() for name in SUPPORTED_FILENAMES
)


def is_requirements_manifest(path: Path) -> bool:
    """Return whether ``path`` is a pip requirements file.

    Matches ``requirements*.txt``, ``*requirements.txt``, and ``*.txt``
    files directly under a ``requirements/`` directory.

    Args:
        path: Candidate manifest path.

    Returns:
        bool: ``True`` when the requirements parser should handle the file.
    """
    name = path.name.lower()
    if name.endswith(".txt") and (
        name.startswith("requirements") or name.endswith("requirements.txt")
    ):
        return True
    return path.parent.name.lower() == "requirements" and name.endswith(".txt")


def is_supported_manifest(path: Path) -> bool:
    """Return whether ``path`` is a known dependency manifest.

    Args:
        path: Candidate manifest path.

    Returns:
        bool: ``True`` when :func:`parse_file` can parse the file.
    """
    return path.name.lower() in _SUPPORTED_NAME_SET or is_requirements_manifest(
        path,
    )


def parse_file(path: Path) -> list[Dependency]:
    """Parse a dependency manifest into dependencies.

    Args:
        path: Path to a supported manifest file.

    Returns:
        list[Dependency]: Parsed dependencies.

    Raises:
        ValueError: When the file name is not a supported manifest.
    """
    name = path.name.lower()
    if name == "pyproject.toml":
        return PyprojectParser().parse(path)
    if name == "package.json":
        return PackageJsonParser().parse(path)
    if name == "cargo.toml":
        return CargoParser().parse(path)
    if is_requirements_manifest(path):
        return RequirementsParser().parse(path)
    msg = f"Unsupported dependency manifest: {path.name}"
    raise ValueError(msg)

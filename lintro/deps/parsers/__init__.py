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
    "parse_file",
]

# Files the validator knows how to parse, in discovery order.
SUPPORTED_FILENAMES: tuple[str, ...] = (
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
)


def parse_file(path: Path) -> list[Dependency]:
    """Parse a dependency manifest into dependencies.

    Args:
        path: Path to a supported manifest file.

    Returns:
        list[Dependency]: Parsed dependencies (empty when unsupported).

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
    if name.startswith("requirements") and name.endswith(".txt"):
        return RequirementsParser().parse(path)
    msg = f"Unsupported dependency manifest: {path.name}"
    raise ValueError(msg)

"""Expand ``[tool.setuptools.packages.find]`` using setuptools itself.

CI import verification and unit tests previously each reimplemented package
discovery (and disagreed on ``fnmatch`` vs ``fnmatchcase`` and whether
ancestor ``__init__.py`` files were required). Asking setuptools is the
same finder the wheel build uses.
"""

from __future__ import annotations

import tomllib
from pathlib import Path


def configured_packages(project_root: Path) -> set[str]:
    """Return package names setuptools would ship for this repository.

    Args:
        project_root: Repository root containing ``pyproject.toml``.

    Returns:
        Package names selected by ``[tool.setuptools.packages.find]``.

    Raises:
        ModuleNotFoundError: If setuptools is not installed in this
            interpreter. The CI importer supplies it with
            ``uv run --no-project --with setuptools==…``.
    """
    try:
        from setuptools.config.expand import find_packages
    except ImportError as exc:
        raise ModuleNotFoundError(
            "Package discovery requires setuptools "
            "(same pin as [build-system] requires).",
        ) from exc
    with (project_root / "pyproject.toml").open("rb") as handle:
        data = tomllib.load(handle)
    find_config = (
        data.get("tool", {}).get("setuptools", {}).get("packages", {}).get("find", {})
    )
    include = tuple(find_config.get("include", ["*"]))
    exclude = tuple(find_config.get("exclude", []))
    namespaces = bool(find_config.get("namespaces", True))
    packages: set[str] = set()
    for where in find_config.get("where", ["."]):
        found = find_packages(
            where=str((project_root / where).resolve()),
            include=include,
            exclude=exclude,
            namespaces=namespaces,
        )
        packages.update(found)
    return packages


def main() -> None:
    """Print one package name per line for CI import verification."""
    root = Path(__file__).resolve().parents[2]
    for name in sorted(configured_packages(project_root=root)):
        print(name)


if __name__ == "__main__":
    main()

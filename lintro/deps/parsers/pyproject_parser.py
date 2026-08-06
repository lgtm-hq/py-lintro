"""Parser for ``pyproject.toml`` dependency tables."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from packaging.requirements import InvalidRequirement, Requirement

from lintro.deps.models import Dependency, Ecosystem
from lintro.deps.parsers._base import build_dependency

__all__ = ["PyprojectParser"]


class PyprojectParser:
    """Parse PEP 621 and Poetry dependency tables from ``pyproject.toml``."""

    def parse(self, path: Path) -> list[Dependency]:
        """Parse dependencies from a ``pyproject.toml`` file.

        Reads ``[project.dependencies]``,
        ``[project.optional-dependencies]``, and
        ``[tool.poetry.dependencies]``.

        Args:
            path: Path to the ``pyproject.toml`` file.

        Returns:
            list[Dependency]: Parsed dependencies.
        """
        with path.open("rb") as handle:
            data = tomllib.load(handle)

        file = str(path)
        deps: list[Dependency] = []

        project = data.get("project", {})
        if isinstance(project, dict):
            deps.extend(self._parse_pep621(project, file))

        poetry = data.get("tool", {}).get("poetry", {})
        if isinstance(poetry, dict):
            deps.extend(self._parse_poetry(poetry, file))

        return deps

    def _parse_pep621(self, project: dict[str, Any], file: str) -> list[Dependency]:
        """Parse PEP 621 ``dependencies`` and ``optional-dependencies``.

        Args:
            project: The ``[project]`` table.
            file: Manifest path string.

        Returns:
            list[Dependency]: Parsed dependencies.
        """
        deps: list[Dependency] = []
        raw: list[str] = []

        if isinstance(project.get("dependencies"), list):
            raw.extend(project["dependencies"])

        optional = project.get("optional-dependencies", {})
        if isinstance(optional, dict):
            for group in optional.values():
                if isinstance(group, list):
                    raw.extend(group)

        for entry in raw:
            dep = self._from_requirement_string(entry, file)
            if dep is not None:
                deps.append(dep)
        return deps

    def _parse_poetry(self, poetry: dict[str, Any], file: str) -> list[Dependency]:
        """Parse ``[tool.poetry.dependencies]`` and group dependencies.

        Args:
            poetry: The ``[tool.poetry]`` table.
            file: Manifest path string.

        Returns:
            list[Dependency]: Parsed dependencies.
        """
        deps: list[Dependency] = []
        tables: list[dict[str, Any]] = []

        main = poetry.get("dependencies", {})
        if isinstance(main, dict):
            tables.append(main)

        # Legacy Poetry table still common in existing projects.
        legacy_dev = poetry.get("dev-dependencies", {})
        if isinstance(legacy_dev, dict):
            tables.append(legacy_dev)

        group = poetry.get("group", {})
        if isinstance(group, dict):
            for spec in group.values():
                if isinstance(spec, dict) and isinstance(
                    spec.get("dependencies"),
                    dict,
                ):
                    tables.append(spec["dependencies"])

        for table in tables:
            for name, constraint in table.items():
                if name.lower() == "python":
                    continue
                version_spec = self._poetry_constraint(constraint)
                if version_spec is None:
                    continue
                deps.append(
                    build_dependency(
                        name=name,
                        version_spec=version_spec,
                        ecosystem=Ecosystem.PYTHON,
                        file=file,
                    ),
                )
        return deps

    @staticmethod
    def _poetry_constraint(constraint: Any) -> str | None:
        """Extract a version string from a Poetry dependency value.

        Args:
            constraint: Raw Poetry value (string or table).

        Returns:
            str | None: The version constraint, or ``None`` to skip
            non-version entries (e.g. git/path dependencies).
        """
        if isinstance(constraint, str):
            return constraint
        if isinstance(constraint, dict):
            version = constraint.get("version")
            if isinstance(version, str):
                return version
        return None

    @staticmethod
    def _from_requirement_string(entry: str, file: str) -> Dependency | None:
        """Build a dependency from a PEP 508 requirement string.

        Args:
            entry: Requirement string (e.g. ``requests>=2.28.0``).
            file: Manifest path string.

        Returns:
            Dependency | None: Parsed dependency, or ``None`` when invalid.
        """
        try:
            requirement = Requirement(entry)
        except InvalidRequirement:
            return None
        return build_dependency(
            name=requirement.name,
            version_spec=str(requirement.specifier),
            ecosystem=Ecosystem.PYTHON,
            file=file,
        )

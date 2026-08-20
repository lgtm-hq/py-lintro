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
    """Parse PEP 621, PEP 735 and Poetry dependency tables."""

    def parse(self, path: Path) -> list[Dependency]:
        """Parse dependencies from a ``pyproject.toml`` file.

        Reads ``[project.dependencies]``,
        ``[project.optional-dependencies]``, PEP 735
        ``[dependency-groups]``, and ``[tool.poetry.dependencies]``.

        Args:
            path: Path to the ``pyproject.toml`` file.

        Returns:
            list[Dependency]: Parsed dependencies.

        Raises:
            ValueError: When any requirement string is not valid PEP 508. The
                check fails closed rather than silently dropping the entry.
        """
        with path.open("rb") as handle:
            data = tomllib.load(handle)

        file = str(path)
        deps: list[Dependency] = []
        invalid: list[str] = []

        project = data.get("project", {})
        if isinstance(project, dict):
            deps.extend(self._parse_pep621(project, file, invalid))

        groups = data.get("dependency-groups", {})
        if isinstance(groups, dict):
            deps.extend(self._parse_dependency_groups(groups, file, invalid))

        poetry = data.get("tool", {}).get("poetry", {})
        if isinstance(poetry, dict):
            deps.extend(self._parse_poetry(poetry, file))

        if invalid:
            joined = ", ".join(repr(entry) for entry in invalid)
            msg = f"invalid requirement specification(s): {joined}"
            raise ValueError(msg)
        return deps

    def _parse_pep621(
        self,
        project: dict[str, Any],
        file: str,
        invalid: list[str],
    ) -> list[Dependency]:
        """Parse PEP 621 ``dependencies`` and ``optional-dependencies``.

        Args:
            project: The ``[project]`` table.
            file: Manifest path string.
            invalid: Accumulator for requirement strings that fail to parse.

        Returns:
            list[Dependency]: Parsed dependencies.
        """
        raw: list[str] = []

        if isinstance(project.get("dependencies"), list):
            raw.extend(project["dependencies"])

        optional = project.get("optional-dependencies", {})
        if isinstance(optional, dict):
            for group in optional.values():
                if isinstance(group, list):
                    raw.extend(group)

        return self._from_requirement_strings(raw, file, invalid)

    def _parse_dependency_groups(
        self,
        groups: dict[str, Any],
        file: str,
        invalid: list[str],
    ) -> list[Dependency]:
        """Parse PEP 735 ``[dependency-groups]`` tables.

        Args:
            groups: The ``[dependency-groups]`` table.
            file: Manifest path string.
            invalid: Accumulator for requirement strings that fail to parse.

        Returns:
            list[Dependency]: Parsed dependencies.
        """
        raw: list[str] = []
        for entries in groups.values():
            if not isinstance(entries, list):
                continue
            # ``{include-group = "..."}`` entries are references, not
            # requirements; the referenced group is parsed on its own.
            raw.extend(entry for entry in entries if isinstance(entry, str))
        return self._from_requirement_strings(raw, file, invalid)

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
    def _from_requirement_strings(
        entries: list[str],
        file: str,
        invalid: list[str],
    ) -> list[Dependency]:
        """Build dependencies from PEP 508 requirement strings.

        Args:
            entries: Requirement strings (e.g. ``requests>=2.28.0``).
            file: Manifest path string.
            invalid: Accumulator for entries that fail to parse.

        Returns:
            list[Dependency]: Parsed dependencies.
        """
        deps: list[Dependency] = []
        for entry in entries:
            try:
                requirement = Requirement(entry)
            except InvalidRequirement:
                invalid.append(entry)
                continue
            deps.append(
                build_dependency(
                    name=requirement.name,
                    version_spec=str(requirement.specifier),
                    ecosystem=Ecosystem.PYTHON,
                    file=file,
                ),
            )
        return deps

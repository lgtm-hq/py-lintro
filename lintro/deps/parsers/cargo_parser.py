"""Parser for Rust ``Cargo.toml`` dependency tables."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from lintro.deps.models import Dependency, Ecosystem
from lintro.deps.parsers._base import build_dependency

__all__ = ["CargoParser"]

_DEP_SECTIONS: tuple[str, ...] = (
    "dependencies",
    "dev-dependencies",
    "build-dependencies",
)


class CargoParser:
    """Parse dependency tables from ``Cargo.toml``."""

    def parse(self, path: Path) -> list[Dependency]:
        """Parse dependencies from a ``Cargo.toml`` file.

        Reads ``dependencies``, ``dev-dependencies``, and
        ``build-dependencies`` at the top level.

        Args:
            path: Path to the ``Cargo.toml`` file.

        Returns:
            list[Dependency]: Parsed dependencies.
        """
        with path.open("rb") as handle:
            data = tomllib.load(handle)

        file = str(path)
        deps: list[Dependency] = []

        for section in _DEP_SECTIONS:
            table = data.get(section)
            if isinstance(table, dict):
                deps.extend(self._from_table(table, file))

        # Platform-specific deps: [target.'cfg(...)'.dependencies]
        target = data.get("target")
        if isinstance(target, dict):
            for target_table in target.values():
                if not isinstance(target_table, dict):
                    continue
                for section in _DEP_SECTIONS:
                    nested = target_table.get(section)
                    if isinstance(nested, dict):
                        deps.extend(self._from_table(nested, file))

        return deps

    def _from_table(
        self,
        table: dict[str, Any],
        file: str,
    ) -> list[Dependency]:
        """Build dependencies from one Cargo dependency table.

        Args:
            table: Mapping of package name to constraint.
            file: Manifest path string.

        Returns:
            list[Dependency]: Parsed dependencies from the table.
        """
        deps: list[Dependency] = []
        for name, constraint in table.items():
            version_spec = self._constraint(constraint)
            if version_spec is None:
                continue
            deps.append(
                build_dependency(
                    name=name,
                    version_spec=version_spec,
                    ecosystem=Ecosystem.CARGO,
                    file=file,
                ),
            )
        return deps

    @staticmethod
    def _constraint(constraint: Any) -> str | None:
        """Extract a version string from a Cargo dependency value.

        Args:
            constraint: Raw Cargo value (string or table).

        Returns:
            str | None: The version constraint, or ``None`` to skip
            git/path dependencies that lack a version.
        """
        if isinstance(constraint, str):
            return constraint
        if isinstance(constraint, dict):
            version = constraint.get("version")
            if isinstance(version, str):
                return version
        return None

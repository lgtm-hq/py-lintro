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
            if not isinstance(table, dict):
                continue
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

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

        Reads ``dependencies``, ``dev-dependencies``,
        ``build-dependencies``, platform-specific ``[target.*]`` tables, and
        ``[workspace.dependencies]``. Member entries declared as
        ``{ workspace = true }`` are resolved against the workspace table when
        it is present in the same manifest.

        Args:
            path: Path to the ``Cargo.toml`` file.

        Returns:
            list[Dependency]: Parsed dependencies.
        """
        with path.open("rb") as handle:
            data = tomllib.load(handle)

        file = str(path)
        deps: list[Dependency] = []

        workspace = data.get("workspace")
        workspace_deps: dict[str, Any] = {}
        if isinstance(workspace, dict) and isinstance(
            workspace.get("dependencies"),
            dict,
        ):
            workspace_deps = workspace["dependencies"]
            deps.extend(self._from_table(workspace_deps, file, {}))

        for section in _DEP_SECTIONS:
            table = data.get(section)
            if isinstance(table, dict):
                deps.extend(self._from_table(table, file, workspace_deps))

        # Platform-specific deps: [target.'cfg(...)'.dependencies]
        target = data.get("target")
        if isinstance(target, dict):
            for target_table in target.values():
                if not isinstance(target_table, dict):
                    continue
                for section in _DEP_SECTIONS:
                    nested = target_table.get(section)
                    if isinstance(nested, dict):
                        deps.extend(self._from_table(nested, file, workspace_deps))

        return self._dedupe(deps)

    @staticmethod
    def _dedupe(deps: list[Dependency]) -> list[Dependency]:
        """Drop repeats of the same name/spec pair.

        A workspace root that both declares ``[workspace.dependencies]`` and
        consumes them via ``{ workspace = true }`` would otherwise report the
        same crate twice.

        Args:
            deps: Parsed dependencies, in discovery order.

        Returns:
            list[Dependency]: Dependencies with duplicates removed.
        """
        seen: set[tuple[str, str]] = set()
        unique: list[Dependency] = []
        for dep in deps:
            key = (dep.name, dep.version_spec)
            if key in seen:
                continue
            seen.add(key)
            unique.append(dep)
        return unique

    def _from_table(
        self,
        table: dict[str, Any],
        file: str,
        workspace_deps: dict[str, Any],
    ) -> list[Dependency]:
        """Build dependencies from one Cargo dependency table.

        Args:
            table: Mapping of package name to constraint.
            file: Manifest path string.
            workspace_deps: ``[workspace.dependencies]`` used to resolve
                ``{ workspace = true }`` entries.

        Returns:
            list[Dependency]: Parsed dependencies from the table.
        """
        deps: list[Dependency] = []
        for name, constraint in table.items():
            version_spec = self._constraint(constraint)
            if version_spec is None and self._inherits_workspace(constraint):
                version_spec = self._constraint(workspace_deps.get(name))
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
    def _inherits_workspace(constraint: Any) -> bool:
        """Return whether an entry inherits its version from the workspace.

        Args:
            constraint: Raw Cargo value (string or table).

        Returns:
            bool: ``True`` for ``{ workspace = true }`` entries.
        """
        return isinstance(constraint, dict) and constraint.get("workspace") is True

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

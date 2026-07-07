"""Parser for npm ``package.json`` dependency maps."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lintro.deps.models import Dependency, Ecosystem
from lintro.deps.parsers._base import build_dependency

__all__ = ["PackageJsonParser"]

_DEP_SECTIONS: tuple[str, ...] = (
    "dependencies",
    "devDependencies",
    "peerDependencies",
    "optionalDependencies",
)


class PackageJsonParser:
    """Parse dependency maps from ``package.json``."""

    def parse(self, path: Path) -> list[Dependency]:
        """Parse dependencies from a ``package.json`` file.

        Reads ``dependencies``, ``devDependencies``,
        ``peerDependencies``, and ``optionalDependencies``.

        Args:
            path: Path to the ``package.json`` file.

        Returns:
            list[Dependency]: Parsed dependencies.
        """
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        file = str(path)
        deps: list[Dependency] = []

        for section in _DEP_SECTIONS:
            table = data.get(section)
            if not isinstance(table, dict):
                continue
            for name, version_spec in table.items():
                if not isinstance(version_spec, str):
                    continue
                # Skip non-registry references (workspaces, git, file paths).
                if any(
                    token in version_spec
                    for token in ("workspace:", "file:", "git", "://", "npm:")
                ):
                    continue
                deps.append(
                    build_dependency(
                        name=name,
                        version_spec=version_spec,
                        ecosystem=Ecosystem.NPM,
                        file=file,
                    ),
                )

        return deps

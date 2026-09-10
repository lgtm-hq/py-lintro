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

# Protocol prefixes that mean "not a registry semver range". Matched as
# prefixes only, to avoid false positives like the ``1.0.0-git.1`` prerelease.
_NON_REGISTRY_PREFIXES: tuple[str, ...] = (
    "workspace:",
    "file:",
    "link:",
    "portal:",
    "npm:",
    "git+",
    "git:",
    "github:",
    "gitlab:",
    "bitbucket:",
    "bitbucket.org:",
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
                if self._is_non_registry(version_spec):
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

    @staticmethod
    def _is_non_registry(version_spec: str) -> bool:
        """Return whether a spec points somewhere other than the registry.

        npm accepts protocol shorthands (``github:owner/repo``) and bare
        ``owner/repo`` GitHub shorthands. These carry no semver comparator, so
        classifying them would report a floating git reference as an exact pin.

        Args:
            version_spec: Raw value from a ``package.json`` dependency map.

        Returns:
            bool: ``True`` when the spec is not a registry semver range.
        """
        lowered = version_spec.strip().lower()
        if lowered.startswith(_NON_REGISTRY_PREFIXES):
            return True
        if "://" in lowered or lowered.endswith(".git"):
            return True
        # Bare ``owner/repo`` (optionally ``owner/repo#ref``) GitHub shorthand.
        # No registry semver range contains a slash.
        return "/" in lowered

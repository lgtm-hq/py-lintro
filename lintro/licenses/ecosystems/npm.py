"""npm ecosystem adapter for collecting package licenses.

Reads license information from a project's ``package.json`` and, when
available, the ``license`` fields of installed packages under
``node_modules``. Operates entirely offline.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lintro.licenses.models import PackageLicense
from lintro.licenses.spdx import normalize_to_spdx


def _extract_license_field(data: dict[str, Any]) -> str | None:
    """Extract a raw license string from a ``package.json`` mapping.

    Supports the modern ``license`` string field and the legacy
    ``licenses`` array of ``{"type": ...}`` objects.

    Args:
        data: Parsed ``package.json`` contents.

    Returns:
        str | None: A raw license string, or None.
    """
    license_value = data.get("license")
    if isinstance(license_value, str) and license_value.strip():
        return license_value.strip()
    if isinstance(license_value, dict):
        type_value = license_value.get("type")
        if isinstance(type_value, str) and type_value.strip():
            return type_value.strip()

    legacy = data.get("licenses")
    if isinstance(legacy, list):
        for entry in legacy:
            if isinstance(entry, dict):
                type_value = entry.get("type")
                if isinstance(type_value, str) and type_value.strip():
                    return type_value.strip()
    return None


class NpmLicenseAdapter:
    """Collect license information for npm packages."""

    ecosystem = "npm"

    def get_licenses_from_package_json(
        self,
        path: Path,
    ) -> list[PackageLicense]:
        """Collect licenses from a ``package.json`` and its ``node_modules``.

        Args:
            path: Path to a ``package.json`` file.

        Returns:
            list[PackageLicense]: Discovered package licenses. Empty when the
                file does not exist or cannot be parsed.
        """
        if not path.is_file():
            return []
        try:
            root_data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return []

        packages: list[PackageLicense] = []
        dev_names = set(root_data.get("devDependencies", {}).keys())

        node_modules = path.parent / "node_modules"
        packages.extend(
            self._collect_node_modules(node_modules, dev_names, str(path)),
        )

        return sorted(packages, key=lambda p: p.name.lower())

    def _collect_node_modules(
        self,
        node_modules: Path,
        dev_names: set[str],
        source: str,
    ) -> list[PackageLicense]:
        """Collect licenses from installed packages under ``node_modules``.

        Handles scoped packages (``@scope/name``) one level deep.

        Args:
            node_modules: Path to the ``node_modules`` directory.
            dev_names: Names declared as dev dependencies in the root manifest.
            source: Source file label recorded on each package.

        Returns:
            list[PackageLicense]: One entry per package manifest found.
        """
        if not node_modules.is_dir():
            return []

        results: list[PackageLicense] = []
        manifests: list[Path] = []
        for child in sorted(node_modules.iterdir()):
            if child.name.startswith("@") and child.is_dir():
                manifests.extend(sorted(child.glob("*/package.json")))
            elif (child / "package.json").is_file():
                manifests.append(child / "package.json")

        for manifest in manifests:
            try:
                data = json.loads(manifest.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            name = data.get("name")
            if not name:
                continue
            raw = _extract_license_field(data)
            results.append(
                PackageLicense(
                    name=name,
                    version=data.get("version", "unknown"),
                    license_id=normalize_to_spdx(raw),
                    license_name=raw,
                    source_file=source,
                    ecosystem=self.ecosystem,
                    is_dev=name in dev_names,
                ),
            )
        return results

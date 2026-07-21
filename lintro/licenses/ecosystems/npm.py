"""npm ecosystem adapter for collecting package licenses.

Reads license information from a project's ``package.json`` and, when
available, the ``license`` fields of installed packages under
``node_modules``. Operates entirely offline.
"""

from __future__ import annotations

import json
from collections import deque
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


def _dep_folder(name: str) -> Path:
    """Return the ``node_modules`` relative path for a dependency name.

    Args:
        name: npm package name, including scoped names.

    Returns:
        Path: Relative path segment under ``node_modules``.
    """
    if name.startswith("@"):
        scope, package = name.split("/", 1)
        return Path(scope) / package
    return Path(name)


def _manifest_for_dep(node_modules: Path, dep_name: str) -> Path | None:
    """Resolve a direct dependency manifest under a ``node_modules`` root.

    Args:
        node_modules: ``node_modules`` directory to search.
        dep_name: Dependency package name.

    Returns:
        Path | None: Path to ``package.json`` when present.
    """
    manifest = node_modules / _dep_folder(dep_name) / "package.json"
    return manifest if manifest.is_file() else None


def _resolve_dependency_manifest(
    parent_manifest: Path,
    dep_name: str,
) -> Path | None:
    """Resolve a dependency manifest reachable from an installed package.

    Searches the package's nested ``node_modules`` first, then walks up
    through ancestor ``node_modules`` directories for hoisted installs.

    Args:
        parent_manifest: Path to the parent package's ``package.json``.
        dep_name: Dependency package name.

    Returns:
        Path | None: Resolved dependency manifest, if installed.
    """
    search_roots: list[Path] = []
    seen: set[Path] = set()
    for ancestor in parent_manifest.parents:
        if ancestor.name == "node_modules" and ancestor not in seen:
            search_roots.append(ancestor)
            seen.add(ancestor)

    for node_modules in search_roots:
        manifest = _manifest_for_dep(node_modules=node_modules, dep_name=dep_name)
        if manifest is not None:
            return manifest
    return None


def _build_dev_prod_map(
    root_data: dict[str, Any],
    node_modules: Path,
) -> dict[Path, bool]:
    """Classify installed manifests as production or development dependencies.

    Walks the installed dependency graph from root ``dependencies`` and
    ``devDependencies``. Transitive ``dependencies``, ``optionalDependencies``,
    ``peerDependencies``, and ``bundled``/``bundleDependencies`` inherit the
    classification of their parent. Production classification wins when a
    package is reachable via both prod and dev paths.

    Args:
        root_data: Parsed root ``package.json`` contents.
        node_modules: Root ``node_modules`` directory.

    Returns:
        dict[Path, bool]: Manifest path to ``is_dev`` flag.
    """
    classification: dict[Path, bool] = {}
    queue: deque[tuple[Path, bool]] = deque()

    for dep_name in root_data.get("dependencies", {}):
        manifest = _manifest_for_dep(node_modules=node_modules, dep_name=dep_name)
        if manifest is not None:
            queue.append((manifest, False))

    for dep_name in root_data.get("devDependencies", {}):
        manifest = _manifest_for_dep(node_modules=node_modules, dep_name=dep_name)
        if manifest is not None:
            queue.append((manifest, True))

    while queue:
        manifest, is_dev = queue.popleft()
        if manifest in classification and not classification[manifest]:
            continue
        if is_dev and manifest not in classification:
            classification[manifest] = True
        elif not is_dev:
            classification[manifest] = False

        try:
            data = json.loads(manifest.read_text())
        except (OSError, json.JSONDecodeError):
            continue

        parent_is_dev = classification[manifest]
        # Follow every edge that installs a nested package, not just runtime
        # ``dependencies``. An ``optionalDependencies``, ``peerDependencies``,
        # or ``bundled``/``bundleDependencies`` child of a dev-only tool is
        # installed under node_modules and must inherit its parent's dev
        # classification; otherwise it falls back to production and
        # ``ignore_dev_dependencies=True`` would still evaluate (and can fail)
        # a dev-only package against the deny policy.
        child_dep_names: set[str] = set()
        for mapping_key in (
            "dependencies",
            "optionalDependencies",
            "peerDependencies",
        ):
            mapping = data.get(mapping_key)
            if isinstance(mapping, dict):
                child_dep_names.update(str(name) for name in mapping)
        # ``bundledDependencies``/``bundleDependencies`` are a list of names.
        for bundled_key in ("bundledDependencies", "bundleDependencies"):
            bundled = data.get(bundled_key)
            if isinstance(bundled, list):
                child_dep_names.update(str(name) for name in bundled)
        for dep_name in child_dep_names:
            child_manifest = _resolve_dependency_manifest(
                parent_manifest=manifest,
                dep_name=dep_name,
            )
            if child_manifest is not None:
                queue.append((child_manifest, parent_is_dev))

    return classification


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

        node_modules = path.parent / "node_modules"
        dev_prod_map = _build_dev_prod_map(
            root_data=root_data,
            node_modules=node_modules,
        )
        packages = self._collect_node_modules(
            node_modules=node_modules,
            dev_prod_map=dev_prod_map,
            source=str(path),
        )

        return sorted(packages, key=lambda p: p.name.lower())

    def _collect_node_modules(
        self,
        node_modules: Path,
        *,
        dev_prod_map: dict[Path, bool],
        source: str,
    ) -> list[PackageLicense]:
        """Collect licenses from installed packages under ``node_modules``.

        Walks nested ``node_modules`` trees (including scoped packages) so
        transitive installs are not skipped.

        Args:
            node_modules: Path to the ``node_modules`` directory.
            dev_prod_map: Manifest path to development-dependency flag.
            source: Source file label recorded on each package.

        Returns:
            list[PackageLicense]: One entry per package manifest found.
        """
        if not node_modules.is_dir():
            return []

        results: list[PackageLicense] = []
        seen: set[tuple[str, str, str]] = set()
        for manifest in sorted(node_modules.rglob("package.json")):
            if manifest.parent == node_modules.parent:
                continue
            if "node_modules" not in manifest.parts:
                continue
            try:
                data = json.loads(manifest.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            name = data.get("name")
            if not name:
                continue
            version = data.get("version", "unknown")
            install_path = str(manifest.parent)
            dedupe_key = (name, version, install_path)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            raw = _extract_license_field(data)
            is_dev = dev_prod_map.get(manifest, False)
            results.append(
                PackageLicense(
                    name=name,
                    version=version,
                    license_id=normalize_to_spdx(raw),
                    license_name=raw,
                    source_file=source,
                    ecosystem=self.ecosystem,
                    is_dev=is_dev,
                ),
            )
        return results

#!/usr/bin/env python3
"""Sync npm package versions with the project version.

The npm distribution ships a meta-package (``npm/lintro``) plus four
platform packages (``npm/<platform>``). Every ``package.json`` carries a
``"version"`` field, and the meta-package additionally pins each
``@lgtm-hq/lintro-<platform>`` optional dependency to the same version. This script
keeps all of them in lock-step with the canonical project version declared
in ``lintro/__init__.py``.

Two modes are supported:

* Write mode (default): rewrite every manifest so its version matches the
  requested version. Used at publish time to inject the release tag.
* Check mode (``--check``): verify every manifest already matches, exiting
  non-zero on drift. Used by CI to catch un-synced manifests.

Usage:
    python scripts/ci/npm/sync_npm_version.py --version 1.2.3
    python scripts/ci/npm/sync_npm_version.py --check
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# Repo root: this file lives at scripts/ci/npm/sync_npm_version.py.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
NPM_DIR = PROJECT_ROOT / "npm"
META_PACKAGE = "lintro"
PLATFORM_PACKAGES = (
    "darwin-arm64",
    "darwin-x64",
    "linux-arm64",
    "linux-x64",
)

_VERSION_RE = re.compile(r'^__version__\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)


@dataclass(frozen=True)
class VersionMismatch:
    """A single manifest field whose version differs from the expected one.

    Attributes:
        manifest: Path to the offending ``package.json`` (repo-relative).
        field: Human-readable field name (e.g. ``"version"``).
        found: The value currently in the manifest.
        expected: The value it should hold.
    """

    manifest: str
    field: str
    found: str
    expected: str


def read_project_version() -> str:
    """Read the canonical project version from ``lintro/__init__.py``.

    Returns:
        The version string (without a leading ``v``).

    Raises:
        ValueError: If the ``__version__`` assignment cannot be found.
    """
    init_path = PROJECT_ROOT / "lintro" / "__init__.py"
    text = init_path.read_text(encoding="utf-8")
    match = _VERSION_RE.search(text)
    if match is None:
        msg = f"Could not find __version__ in {init_path}"
        raise ValueError(msg)
    return match.group(1)


def read_meta_version(npm_dir: Path = NPM_DIR) -> str:
    """Read the version declared by the meta-package manifest.

    Used as the source of truth for internal-consistency checks where the
    in-repo manifests carry a placeholder (``0.0.0-dev``) rather than the
    real release version.

    Args:
        npm_dir: Root ``npm/`` directory.

    Returns:
        The meta-package's ``version`` string.
    """
    data = json.loads(meta_manifest_path(npm_dir).read_text(encoding="utf-8"))
    return str(data.get("version", ""))


def meta_manifest_path(npm_dir: Path = NPM_DIR) -> Path:
    """Return the path to the meta-package manifest.

    Args:
        npm_dir: Root ``npm/`` directory.

    Returns:
        Path to ``npm/lintro/package.json``.
    """
    return npm_dir / META_PACKAGE / "package.json"


def platform_manifest_paths(npm_dir: Path = NPM_DIR) -> list[Path]:
    """Return the paths to every platform-package manifest.

    Args:
        npm_dir: Root ``npm/`` directory.

    Returns:
        A list of ``npm/<platform>/package.json`` paths.
    """
    return [npm_dir / name / "package.json" for name in PLATFORM_PACKAGES]


def all_manifest_paths(npm_dir: Path = NPM_DIR) -> list[Path]:
    """Return every npm manifest path (meta + platforms).

    Args:
        npm_dir: Root ``npm/`` directory.

    Returns:
        A list of all ``package.json`` paths.
    """
    return [meta_manifest_path(npm_dir), *platform_manifest_paths(npm_dir)]


def check_versions(version: str, *, npm_dir: Path = NPM_DIR) -> list[VersionMismatch]:
    """Collect every manifest field that does not match ``version``.

    Args:
        version: The expected version string.
        npm_dir: Root ``npm/`` directory.

    Returns:
        A list of mismatches; empty when every manifest is in sync.
    """
    mismatches: list[VersionMismatch] = []
    for path in all_manifest_paths(npm_dir):
        data = json.loads(path.read_text(encoding="utf-8"))
        rel = str(path.relative_to(npm_dir.parent))
        found = data.get("version", "")
        if found != version:
            mismatches.append(
                VersionMismatch(
                    manifest=rel,
                    field="version",
                    found=found,
                    expected=version,
                ),
            )
        opt_deps = data.get("optionalDependencies", {})
        for dep_name, dep_version in opt_deps.items():
            if dep_name.startswith("@lgtm-hq/lintro-") and dep_version != version:
                mismatches.append(
                    VersionMismatch(
                        manifest=rel,
                        field=f"optionalDependencies.{dep_name}",
                        found=dep_version,
                        expected=version,
                    ),
                )
    return mismatches


def sync_versions(version: str, *, npm_dir: Path = NPM_DIR) -> list[Path]:
    """Rewrite every manifest so its version fields equal ``version``.

    Both top-level ``version`` fields and the meta-package's
    ``@lgtm-hq/lintro-*`` optional-dependency pins are updated. Files are written
    only when their content actually changes.

    Args:
        version: The version string to write.
        npm_dir: Root ``npm/`` directory.

    Returns:
        The list of manifest paths that were modified.
    """
    changed: list[Path] = []
    for path in all_manifest_paths(npm_dir):
        original = path.read_text(encoding="utf-8")
        data = json.loads(original)
        data["version"] = version
        opt_deps = data.get("optionalDependencies")
        if isinstance(opt_deps, dict):
            for dep_name in opt_deps:
                if dep_name.startswith("@lgtm-hq/lintro-"):
                    opt_deps[dep_name] = version
        updated = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        if updated != original:
            path.write_text(updated, encoding="utf-8")
            changed.append(path)
    return changed


def _normalise_version(raw: str) -> str:
    """Strip a leading ``v`` from a tag-style version string.

    Args:
        raw: A version or tag (e.g. ``"v1.2.3"``).

    Returns:
        The version without a leading ``v``.
    """
    return raw[1:] if raw.startswith("v") else raw


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Optional argument vector (defaults to ``sys.argv``).

    Returns:
        Process exit code (0 on success, 1 on drift/failure).
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--version",
        default=None,
        help=(
            "Version to sync to (with or without a leading 'v'). "
            "Defaults to lintro/__init__.py."
        ),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "Verify manifests are in sync instead of writing them. Without "
            "--version this checks internal consistency (all manifests agree "
            "with the meta-package); with --version it checks against that "
            "value (publish-time guard)."
        ),
    )
    args = parser.parse_args(argv)

    if args.check:
        # In-repo manifests carry a placeholder version, so default checks use
        # the meta-package as the source of truth rather than pyproject.
        version = (
            _normalise_version(args.version) if args.version else read_meta_version()
        )
        mismatches = check_versions(version)
        if mismatches:
            print(f"npm manifests out of sync (expected {version}):")
            for mm in mismatches:
                print(f"  {mm.manifest}: {mm.field} = {mm.found!r}")
            print("Run: python scripts/ci/npm/sync_npm_version.py")
            return 1
        print(f"All npm manifests are in sync at version {version}.")
        return 0

    version = _normalise_version(args.version or read_project_version())
    changed = sync_versions(version)
    if changed:
        print(f"Synced {len(changed)} npm manifest(s) to version {version}:")
        for path in changed:
            print(f"  {path.relative_to(PROJECT_ROOT)}")
    else:
        print(f"npm manifests already at version {version}; nothing to do.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

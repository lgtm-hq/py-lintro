#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""Finalize the auto-generated release Version-PR before it is committed.

The release Version-PR generator (lgtm-ci ``reusable-release-version-pr``)
accepts a single repo-side ``version-update-script`` that runs after the version
is stamped and the ``CHANGELOG.md`` is written, but before the version PR is
committed. This orchestrator runs the two repo-side finalizers in order:

1. Reflow ``CHANGELOG.md`` to lintro's 88-column markdown budget so the release
   PR passes dogfooding-lint (#1117), via
   :func:`format_changelog.format_changelog`.
2. Stamp every ``SECURITY.md`` supported-versions table to the current release
   ``major.minor`` line so minor/major bumps do not go stale and break the
   unattended release PR (#1372), via
   :func:`update_security_support.main`.

The finalizer scripts have hyphenated filenames, so they are loaded by path
rather than imported as packages. That job has no Node toolchain and blocks npm
egress, so only the standard library is used here.

Run standalone to finalize the repository in place::

    python scripts/ci/finalize-version-pr.py
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

_SCRIPTS_DIR = Path(__file__).resolve().parent


def _load(module_name: str, filename: str) -> ModuleType:
    """Load a hyphenated sibling script as an importable module.

    Args:
        module_name: Name to register the loaded module under.
        filename: Sibling script filename (e.g. ``"format-changelog.py"``).

    Returns:
        ModuleType: The loaded module.

    Raises:
        ImportError: If the module spec cannot be created or executed.
    """
    path = _SCRIPTS_DIR / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    """Run every repo-side finalizer for the release Version-PR.

    Returns:
        int: The first non-zero finalizer exit code, otherwise ``0``.
    """
    changelog = _load("format_changelog", "format-changelog.py")
    security = _load("update_security_support", "update-security-support.py")

    result = changelog.main([])
    if result != 0:
        return result
    return security.main([])


if __name__ == "__main__":
    raise SystemExit(main())

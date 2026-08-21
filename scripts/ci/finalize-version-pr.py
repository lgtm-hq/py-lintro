#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""Standalone CHANGELOG, SECURITY.md, and pin-sync composer.

The release Version-PR workflow hook is
``scripts/release/prepare_version_artifacts.py`` (the ``version-update-script``
in ``release-version-pr.yml``). That orchestrator runs this composition plus
SPDX license-data refresh. This script remains a local/standalone helper that
does not fetch SPDX data:

1. Reflow ``CHANGELOG.md`` to lintro's 88-column markdown budget so the release
   PR passes dogfooding-lint (#1117), via
   :func:`format_changelog.format_changelog`.
2. Stamp every ``SECURITY.md`` supported-versions table to the current release
   ``major.minor`` line so minor/major bumps do not go stale and break the
   unattended release PR (#1372), via
   :func:`update_security_support.main`.
3. Point the pinned py-lintro release image at the newest published release so
   the digest-lag gate cannot drift (#1590), via
   :func:`sync_pinned_release_image.main`. Deliberately last, and non-fatal:
   it reports problems as warnings and never blocks a release.

The finalizer scripts have hyphenated filenames, so they are loaded by path
rather than imported as packages. Only the standard library is used here.

Run standalone::

    python scripts/ci/finalize-version-pr.py
"""

from __future__ import annotations

import importlib.util
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
    pin_sync = _load("sync_pinned_release_image", "sync-pinned-release-image.py")

    result = int(changelog.main([]))
    if result != 0:
        return result
    result = int(security.main([]))
    if result != 0:
        return result
    # Always 0 by design — the pin sync warns instead of failing, so a
    # registry hiccup or a missing token cannot block an unattended release.
    return int(pin_sync.main([]))


if __name__ == "__main__":
    raise SystemExit(main())

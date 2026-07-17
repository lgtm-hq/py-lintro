#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""Run release Version-PR artifact updates before the bump commit.

Wired as ``version-update-script`` in ``release-version-pr.yml``. Runs:

1. ``scripts/ci/format-changelog.py`` — reflow CHANGELOG.md to 88 columns.
2. ``scripts/ci/update-security-support.py`` — stamp SECURITY.md support table.
3. ``scripts/release/generate_spdx_data.py`` — refresh embedded SPDX license data.
4. ``scripts/ci/sync-pinned-release-image.py`` — non-fatal pin sync (#1590).

Steps 1–3 fail the release job on a non-zero exit. Step 4 warns only.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_module(*, name: str, path: Path) -> ModuleType:
    """Load a Python script as a module from an absolute path.

    Args:
        name: Module name to register.
        path: Script path.

    Returns:
        Loaded module.

    Raises:
        RuntimeError: If the module cannot be loaded.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    """Run changelog, SECURITY.md, SPDX refresh, then non-fatal pin sync.

    Returns:
        Process exit code from the first failing fatal step, or 0 on success.
    """
    changelog_script = REPO_ROOT / "scripts" / "ci" / "format-changelog.py"
    security_script = REPO_ROOT / "scripts" / "ci" / "update-security-support.py"
    spdx_script = REPO_ROOT / "scripts" / "release" / "generate_spdx_data.py"
    pin_script = REPO_ROOT / "scripts" / "ci" / "sync-pinned-release-image.py"

    try:
        changelog = _load_module(name="format_changelog", path=changelog_script)
        security = _load_module(name="update_security_support", path=security_script)
        spdx = _load_module(name="generate_spdx_data", path=spdx_script)
        pin_sync = _load_module(name="sync_pinned_release_image", path=pin_script)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    for module in (changelog, security, spdx):
        rc = int(module.main([]))
        if rc != 0:
            return rc
    # Always 0 by design — pin sync warns instead of failing.
    return int(pin_sync.main([]))


if __name__ == "__main__":
    sys.exit(main())

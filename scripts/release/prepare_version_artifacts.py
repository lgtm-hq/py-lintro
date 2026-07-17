#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""Run release Version-PR artifact updates before the bump commit.

Wired as ``version-update-script`` in ``release-version-pr.yml``. Runs:

1. ``scripts/ci/format-changelog.py`` — reflow CHANGELOG.md to 88 columns.
2. ``scripts/release/generate_spdx_data.py`` — refresh embedded SPDX license data.

Both steps are stdlib-only and fail the release job on a non-zero exit.
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
    """Run changelog formatting then SPDX data regeneration.

    Returns:
        Process exit code from the first failing step, or 0 on success.
    """
    changelog_script = REPO_ROOT / "scripts" / "ci" / "format-changelog.py"
    spdx_script = REPO_ROOT / "scripts" / "release" / "generate_spdx_data.py"

    try:
        changelog = _load_module(name="format_changelog", path=changelog_script)
        spdx = _load_module(name="generate_spdx_data", path=spdx_script)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    changelog_rc = int(changelog.main([]))
    if changelog_rc != 0:
        return changelog_rc
    return int(spdx.main([]))


if __name__ == "__main__":
    sys.exit(main())

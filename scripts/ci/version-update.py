#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""Post-release version bump hook for lgtm-ci ``version-update-script``.

Runs after the release CHANGELOG is written and before the version PR is
committed:

1. Reflow ``CHANGELOG.md`` to lintro's 88-column markdown budget.
2. Sync ``SECURITY.md``, ``.github/SECURITY.md``, and ``docs/pre-commit.md``
   to ``NEXT_VERSION``.

Wired into ``.github/workflows/release-version-pr.yml`` via the reusable
workflow's ``version-update-script`` input.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

_REPO_ROOT = Path(__file__).resolve().parents[2]
_HOOKS = (
    _REPO_ROOT / "scripts" / "ci" / "format-changelog.py",
    _REPO_ROOT / "scripts" / "ci" / "sync-release-docs.py",
)


def _load_module(*, path: Path, module_name: str) -> ModuleType:
    """Load a CI script from disk as an importable module.

    Args:
        path: Absolute path to the script file.
        module_name: Stable module name for importlib.

    Returns:
        ModuleType: The loaded module.

    Raises:
        RuntimeError: If the module cannot be loaded.
    """
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        msg = f"Unable to load module from {path}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv: list[str]) -> int:
    """Run all version-update hooks in order.

    Args:
        argv: Arguments forwarded to :mod:`format_changelog` (optional path).

    Returns:
        int: Process exit code from the first failing hook, or ``0``.
    """
    format_changelog = _load_module(
        path=_HOOKS[0],
        module_name="format_changelog_hook",
    )
    sync_release_docs = _load_module(
        path=_HOOKS[1],
        module_name="sync_release_docs_hook",
    )
    exit_code = int(format_changelog.main(argv))
    if exit_code != 0:
        return exit_code
    return int(sync_release_docs.main([]))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

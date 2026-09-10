#!/usr/bin/env python3
"""Generate ``lintro/_generated_versions.py`` and sync ``manifest.json`` versions.

Thin CLI shim over the importable ``lintro_build.versions`` package, which
holds the generator implementation. Single writer for all tool-version
artifacts derived from ``package.json`` and ``pyproject.toml``; see
``lintro_build/versions/generate.py`` for the full contract.

Modes:
    default: write outputs, exit 0.
    --check: exit 1 with a unified diff if writing would change anything,
             exit 0 if outputs are already in sync, exit 2 on input error.

Stdlib-only on purpose: this script must run in minimal containers without
pip-installed dependencies. Requires Python 3.11+ for ``tomllib``. The
implementation lives in the top-level ``lintro_build`` package; ``sys.path``
is bootstrapped below so it imports cleanly when the script is executed
directly.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Make the top-level ``lintro_build`` package importable when this file is run
# as a script (``python3 scripts/ci/generate-tool-versions.py``).
sys.path.insert(0, str(REPO_ROOT))

from lintro_build.versions.generate import main  # noqa: E402
from lintro_build.versions.paths import GeneratorPaths  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(paths=GeneratorPaths.from_repo_root(REPO_ROOT)))

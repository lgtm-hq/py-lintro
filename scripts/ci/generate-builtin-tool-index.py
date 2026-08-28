#!/usr/bin/env python3
"""Generate ``lintro/plugins/_builtin_index.py``.

Thin CLI shim over the importable ``lintro_build.builtin_index`` module, which
holds the generator implementation. The index makes builtin tool discovery
independent of the filesystem layout so frozen Nuitka onefile binaries keep a
populated registry (#2006); see ``lintro_build/builtin_index.py`` for the full
contract.

Modes:
    default: write the index module, exit 0.
    --check: exit 1 with a unified diff if writing would change anything,
             exit 0 when the committed index is already in sync,
             exit 2 on input error.

Stdlib-only on purpose so it runs in any minimal container without installing
dependencies. The implementation lives in the top-level ``lintro_build``
package; ``sys.path`` is bootstrapped below so it imports cleanly when the
script is executed directly.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Make the top-level ``lintro_build`` package importable when this file is run
# as a script (``python3 scripts/ci/generate-builtin-tool-index.py``).
sys.path.insert(0, str(REPO_ROOT))

from lintro_build.builtin_index import main, resolve_paths  # noqa: E402

if __name__ == "__main__":
    definitions_dir, index_path = resolve_paths(REPO_ROOT)
    sys.exit(
        main(
            definitions_dir=definitions_dir,
            index_path=index_path,
        ),
    )

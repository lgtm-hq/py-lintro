"""Build-time artifact generators for lintro.

Importable implementation behind the ``scripts/ci/generate-tool-versions.py``
and ``scripts/ci/generate-builtin-tool-index.py`` CLI shims. Consolidating the
generators here lets a build backend import and run them without shelling out
(epic #2176).

Stdlib-only by hard constraint: the generators run inside minimal containers
(Renovate, CI) without pip-installed dependencies. Requires Python 3.11+ for
``tomllib``. This package is not shipped in the wheel.
"""

from __future__ import annotations

from pathlib import Path

from . import builtin_index
from .exit_codes import EXIT_DRIFT, EXIT_INPUT_ERROR, EXIT_OK
from .versions.generate import main as _versions_main
from .versions.paths import GeneratorPaths

__all__ = [
    "EXIT_DRIFT",
    "EXIT_INPUT_ERROR",
    "EXIT_OK",
    "GeneratorPaths",
    "generate_all",
]


def generate_all(repo_root: Path, check: bool = False) -> int:
    """Generate every build-time artifact for a repository checkout.

    Runs the tool-version generator and the builtin-index generator against
    ``repo_root``. Both generators always run, so ``--check`` callers see every
    drift diff in one pass.

    Args:
        repo_root: Repository root directory.
        check: When True, report drift instead of writing outputs.

    Returns:
        ``EXIT_INPUT_ERROR`` (2) if either generator hit an input error, else
        ``EXIT_DRIFT`` (1) if either detected drift in check mode, else
        ``EXIT_OK`` (0).
    """
    argv = ["--check"] if check else []
    tools_dir, index_path = builtin_index.resolve_paths(repo_root)
    codes = (
        _versions_main(argv, paths=GeneratorPaths.from_repo_root(repo_root)),
        builtin_index.main(
            argv,
            tools_dir=tools_dir,
            index_path=index_path,
        ),
    )
    if EXIT_INPUT_ERROR in codes:
        return EXIT_INPUT_ERROR
    if EXIT_DRIFT in codes:
        return EXIT_DRIFT
    return EXIT_OK

"""Ratchet on lintro's package-level import cycles.

The `[tool.importlinter]` contracts in ``pyproject.toml`` say which import
directions are legal; they cannot say how far the package still is from that
layering, because every violation that exists today is baselined as an
``ignore_imports`` entry. This test supplies the missing number: the count of
two-way cycles between top-level ``lintro/*`` packages, as measured by
``scripts/ci/import_matrix.py``.

The constant below is a ratchet. It may only go **down**, in the PR that
removes the cycle, and the assertion is ``<=`` so that a refactor never has to
choose between shipping and updating a magic number — a PR that adds a cycle
fails, a PR that removes one is asked to lower the constant.

Function-body imports count. Deferring an import into a function hides it from
the reader, not from the dependency graph, so it is never a way to make this
test pass.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest
from assertpy import assert_that

# Two-way cycles on `main` after #1305 removed `config -> plugins` and the
# eager `cli_utils -> config` edge. Lower this only together with the refactor
# that removes a cycle.
EXPECTED_CYCLES = 9

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE_NAME = "lintro_import_matrix"
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "ci" / "import_matrix.py"
_PACKAGE_ROOT = _REPO_ROOT / "lintro"


def _load_import_matrix_module() -> ModuleType:
    """Import ``scripts/ci/import_matrix.py`` by path.

    The CI scripts directory is not an importable package, so the module is
    loaded from its file location rather than by name. It is registered in
    ``sys.modules`` first because ``@dataclass`` resolves its own module there.

    Returns:
        ModuleType: The loaded module.
    """
    cached = sys.modules.get(_MODULE_NAME)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, _SCRIPT_PATH)
    if spec is None or spec.loader is None:
        pytest.fail(f"could not load the import matrix script at {_SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


def test_import_matrix_script_exists() -> None:
    """The audit script the ratchet depends on is committed."""
    assert_that(_SCRIPT_PATH.is_file()).is_true()


def test_package_import_cycle_count_does_not_grow() -> None:
    """The number of two-way package cycles never exceeds the recorded count."""
    module = _load_import_matrix_module()
    matrix = module.build_matrix(package_root=_PACKAGE_ROOT, root_package="lintro")
    cycles = matrix.two_cycles()

    assert_that(len(cycles)).described_as(
        f"two-way import cycles: {['<->'.join(pair) for pair in cycles]}",
    ).is_less_than_or_equal_to(EXPECTED_CYCLES)


def test_enums_imports_no_other_package() -> None:
    """`lintro.enums` is the bottom layer and imports nothing above it.

    #2290 removed the `enums -> models` edge, the last one. The assertion is
    the whole invariant rather than that one edge, because a new `enums`
    dependency in any direction is the same mistake. `TYPE_CHECKING` imports
    count: `import-linter` and the import matrix both see them.
    """
    module = _load_import_matrix_module()
    matrix = module.build_matrix(package_root=_PACKAGE_ROOT, root_package="lintro")
    outgoing = sorted(
        imported for importer, imported in matrix.edges if importer == "enums"
    )

    assert_that(outgoing).described_as(
        "`lintro.enums` is the bottom layer and must import no other package",
    ).is_empty()

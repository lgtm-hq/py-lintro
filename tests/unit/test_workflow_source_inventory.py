"""Keep workflow-contract sources small and collection unambiguous (#1237).

Split equivalence is one-off review evidence. These live checks allow contract
edits and new cases without maintaining a historical body or case inventory.
"""

from __future__ import annotations

import ast
import importlib
import subprocess  # nosec B404 - collect repository tests without executing them
import sys
from collections.abc import Sequence
from pathlib import Path

_UNIT = Path(__file__).resolve().parent
_ROOT = _UNIT.parents[1]
_MAX_SOURCE_BYTES = 130_000  # Leave headroom below the scanner's 256 KiB limit.


def _workflow_test_paths() -> list[Path]:
    return sorted(
        path for path in _UNIT.glob("test_workflow_*.py") if path != Path(__file__)
    )


def _check_sources(paths: Sequence[Path]) -> None:
    """Reject oversized sources and definitions shadowed within or across files."""
    definitions: dict[str, Path] = {}
    for path in paths:
        assert path.stat().st_size < _MAX_SOURCE_BYTES, f"oversized source: {path}"
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert (
                    node.name not in definitions
                ), f"duplicate definition: {node.name}"
                definitions[node.name] = path


def _check_case_identities(nodeids: Sequence[str]) -> None:
    """Ignore the module prefix so moving a copied case cannot hide duplication."""
    identities = [nodeid.split("::", 1)[1] for nodeid in nodeids]
    assert identities, "no workflow cases collected"
    assert len(identities) == len(set(identities)), "duplicate workflow case identity"


def test_workflow_sources_are_small_and_definitions_unique() -> None:
    """Apply the source contract to all current workflow modules and helpers."""
    _check_sources(
        [*_workflow_test_paths(), _UNIT / "workflow_wiring_helpers.py", Path(__file__)],
    )


def test_workflow_roots_and_documented_constant_export() -> None:
    """Readers resolve this checkout and the README's original import stays valid."""
    for path in [*_workflow_test_paths(), _UNIT / "workflow_wiring_helpers.py"]:
        module = importlib.import_module(f"tests.unit.{path.stem}")
        assert module._REPO_ROOT == _ROOT
    wiring = importlib.import_module("tests.unit.test_workflow_wiring")
    helpers = importlib.import_module("tests.unit.workflow_wiring_helpers")
    assert wiring._AI_CONTRACT_TIER1_CONTEXT == helpers._AI_CONTRACT_TIER1_CONTEXT
    readme = (_ROOT / ".github" / "workflows" / "README.md").read_text(encoding="utf-8")
    assert "_AI_CONTRACT_TIER1_CONTEXT in tests/unit/test_workflow_wiring.py" in readme


def test_workflow_split_mixed_collection_has_unique_cases() -> None:
    """Collect real parameter cases together with an unrelated unit module."""
    paths = _workflow_test_paths()
    result = subprocess.run(  # nosec B603 - fixed collect-only argv
        [
            sys.executable,
            "-m",
            "pytest",
            *(str(path) for path in paths),
            str(_UNIT / "test_ruff_baseline.py"),
            "--collect-only",
            "--no-cov",
            "-q",
            "-o",
            "addopts=",
        ],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    lines = result.stdout.splitlines()
    prefixes = tuple(f"tests/unit/{path.name}::" for path in paths)
    for prefix in (*prefixes, "tests/unit/test_ruff_baseline.py::"):
        assert any(line.startswith(prefix) for line in lines), prefix
    _check_case_identities([line for line in lines if line.startswith(prefixes)])


def test_source_guard_allows_contract_edits_and_new_tests(tmp_path: Path) -> None:
    """Routine assertion edits and additional definitions need no baseline update."""
    source = tmp_path / "test_contract.py"
    source.write_text("def test_contract():\n    assert 1 == 1\n", encoding="utf-8")
    _check_sources([source])
    source.write_text(
        "def test_contract():\n    assert 2 == 2\n\n"
        "def test_new_contract():\n    assert True\n",
        encoding="utf-8",
    )
    _check_sources([source])
    assert len(ast.parse(source.read_text(encoding="utf-8")).body) == 2


def test_source_guard_rejects_size_and_duplicate_mutants(tmp_path: Path) -> None:
    """Check the size boundary and duplicates both within and across modules."""
    import pytest

    first = tmp_path / "first.py"
    second = tmp_path / "second.py"
    first.write_text("#" * (_MAX_SOURCE_BYTES - 1), encoding="utf-8")
    _check_sources([first])
    first.write_text("#" * _MAX_SOURCE_BYTES, encoding="utf-8")
    with pytest.raises(AssertionError, match="oversized source"):
        _check_sources([first])
    definition = "def test_contract():\n    assert True\n"
    first.write_text(definition, encoding="utf-8")
    second.write_text(definition, encoding="utf-8")
    with pytest.raises(AssertionError, match="duplicate definition"):
        _check_sources([first, second])
    first.write_text(definition * 2, encoding="utf-8")
    with pytest.raises(AssertionError, match="duplicate definition"):
        _check_sources([first])


def test_case_guard_allows_new_cases_and_rejects_copied_cases() -> None:
    """New parameters pass while copies in another module remain duplicates."""
    import pytest

    cases = ["a.py::test_contract[first]", "a.py::test_contract[second]"]
    _check_case_identities(cases)
    with pytest.raises(AssertionError, match="duplicate workflow case identity"):
        _check_case_identities([*cases, "b.py::test_contract[first]"])
    with pytest.raises(AssertionError, match="no workflow cases collected"):
        _check_case_identities([])

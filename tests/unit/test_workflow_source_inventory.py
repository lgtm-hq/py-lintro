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

from assertpy import assert_that

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
        assert_that(path.stat().st_size).described_as(
            f"oversized source: {path}",
        ).is_less_than(_MAX_SOURCE_BYTES)
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert_that(definitions).described_as(
                    f"duplicate definition: {node.name}",
                ).does_not_contain_key(node.name)
                definitions[node.name] = path


def _check_case_identities(nodeids: Sequence[str]) -> None:
    """Ignore the module prefix so moving a copied case cannot hide duplication."""
    identities = [nodeid.split("::", 1)[1] for nodeid in nodeids]
    assert_that(identities).described_as("no workflow cases collected").is_not_empty()
    assert_that(identities).described_as(
        "duplicate workflow case identity",
    ).does_not_contain_duplicates()


def test_workflow_sources_are_small_and_definitions_unique() -> None:
    """Apply the source contract to all current workflow modules and helpers."""
    _check_sources(
        [*_workflow_test_paths(), _UNIT / "workflow_wiring_helpers.py", Path(__file__)],
    )


def test_workflow_roots_and_documented_constant_export() -> None:
    """Readers resolve this checkout and the README's original import stays valid."""
    from tests.unit import (
        test_workflow_ai_contracts,
        test_workflow_binaries,
        test_workflow_ci_contracts,
        test_workflow_docker,
        test_workflow_publishing,
        test_workflow_release,
        test_workflow_wiring,
        workflow_wiring_helpers,
    )

    for module in (
        test_workflow_ai_contracts,
        test_workflow_binaries,
        test_workflow_ci_contracts,
        test_workflow_docker,
        test_workflow_publishing,
        test_workflow_release,
        test_workflow_wiring,
        workflow_wiring_helpers,
    ):
        assert_that(module._REPO_ROOT).is_equal_to(_ROOT)
    wiring = importlib.import_module("tests.unit.test_workflow_wiring")
    helpers = importlib.import_module("tests.unit.workflow_wiring_helpers")
    assert_that(wiring._AI_CONTRACT_TIER1_CONTEXT).is_equal_to(
        helpers._AI_CONTRACT_TIER1_CONTEXT,
    )
    readme = (_ROOT / ".github" / "workflows" / "README.md").read_text(encoding="utf-8")
    assert_that(readme).contains(
        "_AI_CONTRACT_TIER1_CONTEXT in tests/unit/test_workflow_wiring.py",
    )


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
    assert_that(result.returncode).described_as(
        result.stdout + result.stderr,
    ).is_equal_to(0)
    lines = result.stdout.splitlines()
    prefixes = tuple(f"tests/unit/{path.name}::" for path in paths)
    for prefix in (*prefixes, "tests/unit/test_ruff_baseline.py::"):
        assert_that(
            [line for line in lines if line.startswith(prefix)],
        ).described_as(prefix).is_not_empty()
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
    assert_that(ast.parse(source.read_text(encoding="utf-8")).body).is_length(2)


def test_source_guard_rejects_size_and_duplicate_mutants(tmp_path: Path) -> None:
    """Check the size boundary and duplicates both within and across modules."""
    first = tmp_path / "first.py"
    second = tmp_path / "second.py"
    first.write_text("#" * (_MAX_SOURCE_BYTES - 1), encoding="utf-8")
    _check_sources([first])
    first.write_text("#" * _MAX_SOURCE_BYTES, encoding="utf-8")
    assert_that(_check_sources).raises(AssertionError).when_called_with(
        [first],
    ).contains("oversized source")
    definition = "def test_contract():\n    assert True\n"
    first.write_text(definition, encoding="utf-8")
    second.write_text(definition, encoding="utf-8")
    assert_that(_check_sources).raises(AssertionError).when_called_with(
        [first, second],
    ).contains("duplicate definition")
    first.write_text(definition * 2, encoding="utf-8")
    assert_that(_check_sources).raises(AssertionError).when_called_with(
        [first],
    ).contains("duplicate definition")


def test_case_guard_allows_new_cases_and_rejects_copied_cases() -> None:
    """New parameters pass while copies in another module remain duplicates."""
    cases = ["a.py::test_contract[first]", "a.py::test_contract[second]"]
    _check_case_identities(cases)
    assert_that(_check_case_identities).raises(AssertionError).when_called_with(
        [*cases, "b.py::test_contract[first]"],
    ).contains("duplicate workflow case identity")
    assert_that(_check_case_identities).raises(AssertionError).when_called_with(
        [],
    ).contains("no workflow cases collected")

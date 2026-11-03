"""Keep the workflow-contract split complete and small (#1237).

The inventory records the pre-split AST and actual pytest parameter cases at
its named base commit. Intentional contract edits must update the corresponding
inventory entry; moves and formatting alone must preserve it.
"""

from __future__ import annotations

import ast
import hashlib
import importlib
import json
import subprocess  # nosec B404 - collect this repository's tests, without executing them
import sys
from collections import Counter
from pathlib import Path

_UNIT = Path(__file__).resolve().parent
_ROOT = _UNIT.parents[1]
_INVENTORY = _UNIT / "workflow_wiring_inventory.json"


def test_workflow_split_preserves_definition_bodies_and_symbols() -> None:
    """Every original test, decorator, fixture and helper occurs exactly once."""
    baseline = json.loads(_INVENTORY.read_text(encoding="utf-8"))
    paths = [_UNIT / f"{name}.py" for name in baseline["modules"]]
    paths.append(_UNIT / "workflow_wiring_helpers.py")
    definitions: dict[str, str] = {}
    for path in paths:
        assert path.stat().st_size < 130_000, path
        module = importlib.import_module(f"tests.unit.{path.stem}")
        assert module._REPO_ROOT == _ROOT
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.FunctionDef):
                assert node.name not in definitions, node.name
                definitions[node.name] = hashlib.sha256(
                    ast.dump(node, include_attributes=False).encode(),
                ).hexdigest()
                assert callable(getattr(module, node.name)), node.name
    assert definitions == baseline["definitions"]
    assert _INVENTORY.stat().st_size < 130_000
    assert Path(__file__).stat().st_size < 130_000
    wiring = importlib.import_module("tests.unit.test_workflow_wiring")
    helpers = importlib.import_module("tests.unit.workflow_wiring_helpers")
    # The workflow README names this symbol at its original module path.
    assert wiring._AI_CONTRACT_TIER1_CONTEXT == helpers._AI_CONTRACT_TIER1_CONTEXT


def test_workflow_split_mixed_collection_preserves_every_parameter_case() -> None:
    """Mixed-module collection contains each original case exactly once."""
    baseline = json.loads(_INVENTORY.read_text(encoding="utf-8"))
    result = (
        subprocess.run(  # nosec B603 - fixed collect-only argv, never executes tests
            [
                sys.executable,
                "-m",
                "pytest",
                *(str(_UNIT / f"{name}.py") for name in baseline["modules"]),
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
            timeout=90,
        )
    )
    assert result.returncode == 0, result.stdout + result.stderr
    prefixes = tuple(f"tests/unit/{name}.py::" for name in baseline["modules"])
    collected = [
        line.split("::", 1)[1]
        for line in result.stdout.splitlines()
        if line.startswith(prefixes)
    ]
    assert Counter(collected) == Counter(baseline["cases"])
    assert len(collected) == len(set(collected))

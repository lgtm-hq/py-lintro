# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""Ratchets for the two test-suite hygiene scanners (#2315).

Both scanners were written to burn down a backlog: 16 groups of copy-pasted
test bodies left behind by earlier file splits, and 125 tests whose only
assertions read mock call bookkeeping. Once cleared, these tests keep the
counts at zero so the same debt cannot creep back in.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from assertpy import assert_that

ROOT = Path(__file__).resolve().parents[4]
SCANNER_DIR = ROOT / "scripts" / "ci" / "testing"


def _load(name: str) -> ModuleType:
    """Import one scanner module by path.

    Args:
        name: Module file stem under ``scripts/ci/testing``.

    Returns:
        The imported module.

    Raises:
        RuntimeError: If the module cannot be loaded from its path.
    """
    path = SCANNER_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load scanner module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_no_test_function_bodies_are_duplicated() -> None:
    """No two test functions share a body, arguments and module context."""
    scanner = _load("scan_duplicate_test_bodies")
    groups = scanner.find_duplicate_groups()
    rendered = [[f"{f.path}:{f.lineno} {f.name}" for f in group] for group in groups]
    assert_that(rendered).is_empty()


def test_no_test_asserts_only_on_mock_bookkeeping() -> None:
    """Every test asserts on something other than how a mock was called."""
    scanner = _load("scan_mock_only_tests")
    offenders = scanner.find_mock_only_tests()
    rendered = [f"{t.path}:{t.lineno} {t.name}" for t in offenders]
    assert_that(rendered).is_empty()

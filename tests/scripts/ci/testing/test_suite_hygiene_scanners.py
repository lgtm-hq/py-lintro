# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""Ratchets and positive controls for the two hygiene scanners (#2315).

Both scanners were written to burn down a backlog: 16 groups of copy-pasted
test bodies left behind by earlier file splits, and 257 tests whose only
assertions read mock call bookkeeping. Once cleared, the ratchets keep the live
counts at zero so the same debt cannot creep back in.

A zero count alone cannot tell a clean suite from a scanner that always returns
nothing, so each scanner is also pointed at a canary directory it *must* flag.
The canary sources live under ``canary/`` with a ``.py.txt`` suffix: pytest
collects only ``test_*.py``, and the scanners sweep ``rglob("*.py")``, so
neither picks them up until a test copies them into ``tmp_path`` (#2375).
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path
from types import ModuleType

import pytest
from assertpy import assert_that

ROOT = Path(__file__).resolve().parents[4]
SCANNER_DIR = ROOT / "scripts" / "ci" / "testing"
CANARY_DIR = Path(__file__).parent / "canary"


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


def _plant_canary(*, stem: str, tmp_path: Path) -> Path:
    """Copy one ``.py.txt`` canary source into ``tmp_path`` as a module.

    Args:
        stem: Canary file stem under ``canary/``, without any suffix.
        tmp_path: Pytest temporary directory to plant the module in.

    Returns:
        The directory holding the planted module, ready to scan.
    """
    planted = tmp_path / "canary"
    planted.mkdir()
    shutil.copyfile(CANARY_DIR / f"{stem}.py.txt", planted / f"{stem}.py")
    return planted


# =============================================================================
# Live-tree ratchets
# =============================================================================


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


# =============================================================================
# Positive controls: a scanner that always returned nothing must fail these
# =============================================================================


def test_duplicate_scanner_reports_a_planted_duplicate_pair(tmp_path: Path) -> None:
    """The duplicate scanner groups the two copies and leaves the third alone.

    Args:
        tmp_path: Pytest temporary directory holding the planted canary.
    """
    scanner = _load("scan_duplicate_test_bodies")

    groups = scanner.find_duplicate_groups(
        root=_plant_canary(stem="canary_duplicates", tmp_path=tmp_path),
    )

    assert_that(groups).is_length(1)
    assert_that(sorted(function.name for function in groups[0])).is_equal_to(
        ["test_canary_copy", "test_canary_original"],
    )


def test_mock_only_scanner_reports_planted_bookkeeping_tests(tmp_path: Path) -> None:
    """The mock-only scanner flags the direct helper and the tainted local.

    The observable-result, ``pytest.raises`` and attribute-assignment canaries
    must stay unflagged, which pins the two exemptions the scanner grew during
    the sweep plus the ``Store``-context-only taint rule.

    Args:
        tmp_path: Pytest temporary directory holding the planted canary.
    """
    scanner = _load("scan_mock_only_tests")

    offenders = scanner.find_mock_only_tests(
        root=_plant_canary(stem="canary_mock_only", tmp_path=tmp_path),
    )

    assert_that(sorted(test.name for test in offenders)).is_equal_to(
        ["test_canary_direct_mock_assert", "test_canary_tainted_local"],
    )


# =============================================================================
# A mistyped --root must not report a clean gate
# =============================================================================


@pytest.mark.parametrize(
    "module_name",
    ["scan_duplicate_test_bodies", "scan_mock_only_tests"],
    ids=["duplicates", "mock-only"],
)
def test_scanner_rejects_a_root_that_is_not_a_directory(
    module_name: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A ``--root`` that is not a directory exits 2 instead of reporting zero.

    Args:
        module_name: Scanner module stem under ``scripts/ci/testing``.
        tmp_path: Pytest temporary directory providing a path that is absent.
        capsys: Pytest capture fixture for the error message.
    """
    scanner = _load(module_name)
    missing = tmp_path / "does-not-exist"

    exit_code = scanner.main(["--root", str(missing)])

    assert_that(exit_code).is_equal_to(2)
    assert_that(capsys.readouterr().err).contains("not a directory")


@pytest.mark.parametrize(
    ("module_name", "finder"),
    [
        ("scan_duplicate_test_bodies", "find_duplicate_groups"),
        ("scan_mock_only_tests", "find_mock_only_tests"),
    ],
    ids=["duplicates", "mock-only"],
)
def test_scanner_library_call_raises_on_a_missing_root(
    module_name: str,
    finder: str,
    tmp_path: Path,
) -> None:
    """Library callers get an error rather than a false clean result.

    Args:
        module_name: Scanner module stem under ``scripts/ci/testing``.
        finder: Name of the module-level scan function to call.
        tmp_path: Pytest temporary directory providing a path that is absent.
    """
    scanner = _load(module_name)

    with pytest.raises(NotADirectoryError):
        getattr(scanner, finder)(root=tmp_path / "does-not-exist")


# =============================================================================
# The CLI entry point must report what the library call found
# =============================================================================


@pytest.mark.parametrize(
    ("module_name", "canary_stem"),
    [
        ("scan_duplicate_test_bodies", "canary_duplicates"),
        ("scan_mock_only_tests", "canary_mock_only"),
    ],
    ids=["duplicates", "mock-only"],
)
def test_scanner_main_exits_one_on_a_planted_canary(
    module_name: str,
    canary_stem: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``main`` prints the offenders it found and exits ``1``.

    A ``main`` that dropped the findings on the floor and always returned
    ``0`` would still satisfy the ``--root`` validation tests, so the
    print-and-fail branch needs a canary of its own.

    Args:
        module_name: Scanner module stem under ``scripts/ci/testing``.
        canary_stem: Canary file stem under ``canary/``, without any suffix.
        tmp_path: Pytest temporary directory holding the planted canary.
        capsys: Pytest capture fixture for the printed report.
    """
    scanner = _load(module_name)
    planted = _plant_canary(stem=canary_stem, tmp_path=tmp_path)

    exit_code = scanner.main(["--root", str(planted)])

    assert_that(exit_code).is_equal_to(1)
    assert_that(capsys.readouterr().out).contains("test_canary")


@pytest.mark.parametrize(
    "module_name",
    ["scan_duplicate_test_bodies", "scan_mock_only_tests"],
    ids=["duplicates", "mock-only"],
)
def test_scanner_main_exits_zero_on_a_clean_tree(
    module_name: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``main`` exits ``0`` and reports a zero count on an empty tree.

    Args:
        module_name: Scanner module stem under ``scripts/ci/testing``.
        tmp_path: Pytest temporary directory standing in for a clean tree.
        capsys: Pytest capture fixture for the printed report.
    """
    scanner = _load(module_name)

    exit_code = scanner.main(["--root", str(tmp_path)])

    assert_that(exit_code).is_equal_to(0)
    assert_that(capsys.readouterr().out).contains("0 ")

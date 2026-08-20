"""Tests for package-level lazy exports."""

from __future__ import annotations

import ast
import importlib
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from assertpy import assert_that

import lintro.ai.review as review_pkg

_REVIEW_PACKAGE = "lintro.ai.review"


def _review_module_names() -> list[str]:
    """Return the currently imported ``lintro.ai.review`` module names.

    Returns:
        The package itself plus every imported submodule.
    """
    return [
        name
        for name in list(sys.modules)
        if name == _REVIEW_PACKAGE or name.startswith(f"{_REVIEW_PACKAGE}.")
    ]


@pytest.fixture(autouse=True)
def restore_review_modules() -> Iterator[None]:
    """Undo the ``sys.modules`` surgery these tests perform.

    ``test_lazy_exports_match_implementation`` deletes every lazy-export source
    module and lets the package re-import it. Re-importing a module defines
    *new* class objects, so a module that already imported, say,
    ``ReviewContextError`` eagerly keeps the old class and its ``except``
    clauses silently stop matching for the rest of the session. Restoring the
    original modules — and the package globals that cache their attributes —
    keeps that damage inside this file.

    Yields:
        None: Control returns to the test while the snapshot is held.
    """
    tracked = {name: sys.modules[name] for name in _review_module_names()}
    package_globals = dict(vars(review_pkg))
    try:
        yield
    finally:
        sys.modules.update(tracked)
        vars(review_pkg).clear()
        vars(review_pkg).update(package_globals)


@pytest.mark.parametrize("export_name", review_pkg.__all__)
def test_package_exports_resolve(export_name: str) -> None:
    """Every public export resolves via the package lazy loader."""
    importlib.reload(review_pkg)
    exported = getattr(review_pkg, export_name)
    assert_that(exported).is_not_none()


def test_lazy_exports_match_implementation() -> None:
    """Lazy exports resolve to objects defined in their source modules."""
    importlib.reload(review_pkg)
    for export_name, (module_name, attr_name) in review_pkg._LAZY_EXPORTS.items():
        vars(review_pkg).pop(export_name, None)
        if module_name in sys.modules:
            del sys.modules[module_name]
        assert_that(module_name in sys.modules).is_false()
        resolved = getattr(review_pkg, export_name)
        # getattr loads the source module via the package __getattr__ map.
        assert_that(module_name in sys.modules).is_true()
        source_module = sys.modules[module_name]
        assert_that(resolved).is_equal_to(getattr(source_module, attr_name))


def test_package_exports_include_changed_file_status() -> None:
    """ChangedFileStatus is part of the public package surface."""
    importlib.reload(review_pkg)
    assert_that(review_pkg.__all__).contains("ChangedFileStatus")
    assert_that(review_pkg.ChangedFileStatus).is_not_none()


def test_lazy_export_names_match_runtime_map() -> None:
    """Public exports are exactly the runtime lazy-import map keys."""
    importlib.reload(review_pkg)
    assert_that(set(review_pkg.__all__)).is_equal_to(set(review_pkg._LAZY_EXPORTS))


def test_dir_includes_lazy_export_names() -> None:
    """``dir()`` lists lazy exports before they have been accessed."""
    importlib.reload(review_pkg)
    assert_that(set(review_pkg.__all__).issubset(set(dir(review_pkg)))).is_true()


def test_type_checking_lazy_exports_match_runtime_map() -> None:
    """TYPE_CHECKING imports stay aligned with the lazy export map."""
    source = Path(review_pkg.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    type_checking_names: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if not isinstance(test, ast.Name) or test.id != "TYPE_CHECKING":
            continue
        for sub in node.body:
            if not isinstance(sub, ast.ImportFrom):
                continue
            for alias in sub.names:
                type_checking_names.add(alias.asname or alias.name)

    assert_that(type_checking_names).is_equal_to(set(review_pkg._LAZY_EXPORTS))

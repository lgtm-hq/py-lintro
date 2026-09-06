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


@pytest.fixture(autouse=True)
def _restore_review_package() -> Iterator[None]:
    """Undo the reloads and ``sys.modules`` evictions these tests perform.

    Every test here reloads ``lintro.ai.review`` and one of them deletes each
    lazy target from ``sys.modules`` so the loader has to import it again.
    Left in place, a later import binds a *different* function object than the
    one a neighbour already captured, which broke identity assertions once the
    suite started running in randomised order (#2315). Snapshot the package
    namespace and the affected modules, and put both back.

    Yields:
        None: Restores the package namespace and ``sys.modules``.
    """
    targets = {module for module, _ in review_pkg._LAZY_EXPORTS.values()}
    original_namespace = dict(vars(review_pkg))
    original_modules = {
        name: sys.modules[name] for name in targets if name in sys.modules
    }
    try:
        yield
    finally:
        importlib.reload(review_pkg)
        vars(review_pkg).clear()
        vars(review_pkg).update(original_namespace)
        for name, module in original_modules.items():
            sys.modules[name] = module


@pytest.mark.parametrize("export_name", review_pkg.__all__)
def test_package_exports_resolve(export_name: str) -> None:
    """Every public export resolves via the package lazy loader."""
    importlib.reload(review_pkg)
    exported = getattr(review_pkg, export_name)
    assert_that(exported).is_not_none()


def test_lazy_exports_match_implementation() -> None:
    """Lazy exports resolve to callables defined in their source modules."""
    importlib.reload(review_pkg)
    for export_name, (module_name, attr_name) in review_pkg._LAZY_EXPORTS.items():
        vars(review_pkg).pop(export_name, None)
        if module_name in sys.modules:
            del sys.modules[module_name]
        assert_that(module_name in sys.modules).is_false()
        resolved = getattr(review_pkg, export_name)
        assert_that(resolved.__module__).is_equal_to(module_name)
        assert_that(resolved.__name__).is_equal_to(attr_name)


def test_package_exports_include_changed_file_status() -> None:
    """ChangedFileStatus is part of the public package surface."""
    importlib.reload(review_pkg)
    assert_that(review_pkg.__all__).contains("ChangedFileStatus")
    assert_that(review_pkg.ChangedFileStatus).is_not_none()


def test_lazy_export_names_match_runtime_map() -> None:
    """Lazy export names are derived from the runtime lazy-import map."""
    importlib.reload(review_pkg)
    static_exports = {
        "BUILTIN_CHECKLIST_ITEMS",
        "ChangedFile",
        "ChangedFileStatus",
        "ChecklistItem",
        "ChunkingResult",
        "FileClassification",
        "FileDomain",
        "PRMetadata",
        "REL_DIRECTORY_PREFIX",
        "REL_SINGLE_FILE",
        "REL_SOURCE_TEST",
        "REL_WORKFLOW_SCRIPT_TEST",
        "RelationshipLabel",
        "ReviewCategory",
        "ReviewChunk",
        "ReviewContext",
        "ReviewContextError",
        "ReviewContextErrorCode",
        "format_checklist_for_prompt",
        "get_all_checklist_items",
        "select_checklist_items",
    }
    assert_that(set(review_pkg.__all__)).is_equal_to(
        static_exports | set(review_pkg._LAZY_EXPORTS),
    )


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

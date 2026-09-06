"""Guard against ``TYPE_CHECKING``-only names used in runtime annotations.

Without ``from __future__ import annotations``, a function's parameter and
return annotations are evaluated when the ``def`` executes, and a module- or
class-level ``x: Foo`` is evaluated at import. A name imported only under
``if TYPE_CHECKING:`` does not exist then, so the module raises ``NameError``
on import.

Python 3.14 hides this: PEP 649 makes annotations lazy, so a developer on 3.14
sees a green local run while 3.11-3.13 fail at import (#1305 shipped exactly
that bug through a `TYPE_CHECKING`-only `rich.table.Table`). This test finds it
on any interpreter.
"""

from __future__ import annotations

import ast
from pathlib import Path

from assertpy import assert_that

LINTRO_ROOT = Path(__file__).resolve().parents[2] / "lintro"


def _has_future_annotations(tree: ast.Module) -> bool:
    """Report whether the module defers annotation evaluation.

    Args:
        tree: Parsed module.

    Returns:
        bool: ``True`` when ``from __future__ import annotations`` is present.
    """
    return any(
        isinstance(node, ast.ImportFrom)
        and node.module == "__future__"
        and any(alias.name == "annotations" for alias in node.names)
        for node in tree.body
    )


def _type_checking_only_names(tree: ast.Module) -> set[str]:
    """Collect names bound only inside ``if TYPE_CHECKING:`` blocks.

    Args:
        tree: Parsed module.

    Returns:
        set[str]: Names that do not exist at runtime.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        guarded = (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
            isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
        )
        if not guarded:
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Import | ast.ImportFrom):
                for alias in inner.names:
                    names.add(alias.asname or alias.name.split(".")[0])
    return names


def _runtime_annotations(tree: ast.Module) -> list[ast.expr]:
    """Collect annotation expressions Python evaluates eagerly.

    Args:
        tree: Parsed module.

    Returns:
        list[ast.expr]: Parameter, return, and annotated-assignment
        annotations. Names inside quoted (string) annotations are not parsed,
        so those are excluded for free.
    """
    annotations: list[ast.expr] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            args = node.args
            candidates = [
                *args.posonlyargs,
                *args.args,
                *args.kwonlyargs,
                args.vararg,
                args.kwarg,
            ]
            annotations.extend(
                arg.annotation
                for arg in candidates
                if arg is not None and arg.annotation is not None
            )
            if node.returns is not None:
                annotations.append(node.returns)
        elif isinstance(node, ast.AnnAssign) and node.annotation is not None:
            annotations.append(node.annotation)
    return annotations


def test_no_type_checking_name_is_evaluated_at_runtime() -> None:
    """No module evaluates a ``TYPE_CHECKING``-only name in an annotation."""
    violations: list[str] = []
    for path in sorted(LINTRO_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if _has_future_annotations(tree):
            continue
        deferred = _type_checking_only_names(tree)
        if not deferred:
            continue
        relative = path.relative_to(LINTRO_ROOT.parent).as_posix()
        for annotation in _runtime_annotations(tree):
            for node in ast.walk(annotation):
                if isinstance(node, ast.Name) and node.id in deferred:
                    violations.append(
                        f"{relative}:{node.lineno} evaluates TYPE_CHECKING-only "
                        f"name {node.id!r}; add `from __future__ import "
                        f"annotations` or quote the annotation",
                    )

    assert_that(violations).is_empty()

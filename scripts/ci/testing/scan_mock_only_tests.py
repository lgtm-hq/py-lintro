#!/usr/bin/env python3
"""Report test functions whose only assertions inspect mock bookkeeping.

A test that asserts nothing but ``mock.assert_called_once_with(...)`` or
``assert_that(mock.call_count)`` passes whatever the production code computes:
it pins the call graph the test itself wired up, not observable behaviour.
This scanner walks ``tests/``, classifies every assertion in each test function
and reports the functions where every assertion is mock bookkeeping.

Run as a script for a human-readable report, or import :func:`find_mock_only_tests`
from a test to assert the count stays at zero.
"""

from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

#: Mock assertion helper methods (``mock.assert_called_once_with`` and friends).
_MOCK_ASSERT_PREFIX = "assert_"

#: Mock attributes that only report how a mock was called.
_MOCK_CALL_ATTRIBUTES = frozenset(
    {
        "call_args",
        "call_args_list",
        "call_count",
        "called",
        "mock_calls",
        "await_args",
        "await_args_list",
        "await_count",
        "awaited",
    },
)


@dataclass(frozen=True)
class MockOnlyTest:
    """One test function whose assertions are all mock bookkeeping.

    Attributes:
        path: Repository-relative path of the file defining the function.
        name: Function name.
        lineno: 1-based line number of the ``def`` statement.
    """

    path: str
    name: str
    lineno: int


def _display_path(*, path: Path) -> str:
    """Render a path relative to the repository when possible.

    Args:
        path: File path to render.

    Returns:
        The repository-relative path, or the absolute path when the file lies
        outside the repository (``--root`` may point anywhere).
    """
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _mentions_call_attribute(node: ast.AST) -> bool:
    """Report whether a node reads a mock call-bookkeeping attribute.

    Args:
        node: Node to inspect.

    Returns:
        ``True`` when the node reads one of the mock call attributes.
    """
    return any(
        isinstance(child, ast.Attribute) and child.attr in _MOCK_CALL_ATTRIBUTES
        for child in ast.walk(node)
    )


def _is_mock_assert_call(node: ast.Call) -> bool:
    """Report whether a call is a ``mock.assert_*`` helper invocation.

    Args:
        node: Call node to inspect.

    Returns:
        ``True`` for ``assert_called``/``assert_not_called``/``assert_awaited``
        style helpers, ``False`` for anything else (``assert_that`` included).
    """
    func = node.func
    if not isinstance(func, ast.Attribute):
        return False
    name = func.attr
    return name.startswith(_MOCK_ASSERT_PREFIX) and name != "assert_that"


def _is_pytest_raises(node: ast.stmt) -> bool:
    """Report whether a statement is a ``with pytest.raises(...)`` block.

    Asserting that a call raises is an assertion on observable behaviour, but
    it is a ``With`` statement rather than an ``Assert`` or a call expression,
    so it needs recognising explicitly.

    Args:
        node: Statement to inspect.

    Returns:
        ``True`` for a ``with``/``async with`` whose context manager is
        ``pytest.raises`` or ``pytest.warns``.
    """
    if not isinstance(node, ast.With | ast.AsyncWith):
        return False
    for item in node.items:
        expression = item.context_expr
        if not isinstance(expression, ast.Call):
            continue
        func = expression.func
        if isinstance(func, ast.Attribute) and func.attr in {"raises", "warns"}:
            return True
    return False


def _tainted_names(func: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Collect locals whose value comes from mock call bookkeeping.

    ``captured = mock.call_args.kwargs["cmd"]`` followed by
    ``assert_that(captured)`` is still an assertion about how a mock was
    called, so the intermediate name has to carry the taint.

    Args:
        func: Function definition to scan.

    Returns:
        The names bound to an expression that reads a mock call attribute.
    """
    tainted: set[str] = set()
    for node in ast.walk(func):
        value = getattr(node, "value", None)
        if not isinstance(node, ast.Assign | ast.AnnAssign) or value is None:
            continue
        if not (
            _mentions_call_attribute(node=value)
            or _reads_tainted(
                node=value,
                tainted=tainted,
            )
        ):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            for name in ast.walk(target):
                if isinstance(name, ast.Name):
                    tainted.add(name.id)
    return tainted


def _reads_tainted(node: ast.AST, tainted: set[str]) -> bool:
    """Report whether a node reads one of the tainted names.

    Args:
        node: Node to inspect.
        tainted: Names known to carry mock call bookkeeping.

    Returns:
        ``True`` when the node loads a tainted name.
    """
    return any(
        isinstance(child, ast.Name)
        and isinstance(child.ctx, ast.Load)
        and child.id in tainted
        for child in ast.walk(node)
    )


def _assertion_kind(node: ast.stmt, tainted: set[str]) -> str | None:
    """Classify one statement as an assertion.

    Args:
        node: Statement to classify.
        tainted: Local names carrying mock call bookkeeping.

    Returns:
        ``"mock"`` for a mock-bookkeeping assertion, ``"real"`` for an
        assertion on an observable value, or ``None`` when the statement is
        not an assertion at all.
    """
    if _is_pytest_raises(node=node):
        return "real"
    if isinstance(node, ast.Assert):
        return _kind_for(node=node.test, tainted=tainted)
    if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
        return None
    call = node.value
    root = call
    while isinstance(root.func, ast.Attribute) and isinstance(
        root.func.value,
        ast.Call,
    ):
        root = root.func.value
    is_assert_that = isinstance(root.func, ast.Name) and root.func.id == "assert_that"
    if is_assert_that:
        return _kind_for(node=call, tainted=tainted)
    if _is_mock_assert_call(node=call):
        return "mock"
    return None


def _kind_for(node: ast.AST, tainted: set[str]) -> str:
    """Classify the subject of one assertion.

    Args:
        node: Expression the assertion inspects.
        tainted: Local names carrying mock call bookkeeping.

    Returns:
        ``"mock"`` when the expression reads mock call bookkeeping directly or
        through a tainted local, else ``"real"``.
    """
    reads_mock = _mentions_call_attribute(node=node) or _reads_tainted(
        node=node,
        tainted=tainted,
    )
    return "mock" if reads_mock else "real"


def _iter_test_functions(
    tree: ast.AST,
) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Collect the test functions defined anywhere in a module.

    Args:
        tree: Parsed module.

    Returns:
        Every ``test_``-prefixed function definition in the module.
    """
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name.startswith("test_")
    ]


def _is_mock_only(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Report whether every assertion in a test is mock bookkeeping.

    Args:
        func: Test function definition.

    Returns:
        ``True`` when the function has at least one assertion and all of them
        inspect mock call bookkeeping.
    """
    tainted = _tainted_names(func=func)
    kinds = [
        kind
        for node in ast.walk(func)
        if isinstance(node, ast.stmt)
        and (kind := _assertion_kind(node=node, tainted=tainted)) is not None
    ]
    return bool(kinds) and all(kind == "mock" for kind in kinds)


def find_mock_only_tests(*, root: Path | None = None) -> list[MockOnlyTest]:
    """Find test functions whose assertions are all mock bookkeeping.

    Args:
        root: Directory to scan. Defaults to the repository ``tests`` tree.

    Returns:
        The offending test functions, sorted by path and line number.

    Raises:
        NotADirectoryError: If ``root`` is not an existing directory. Left to
            ``rglob`` this would yield nothing and report a clean gate, so a
            typo in CI would silently pass (#2375).
    """
    scan_root = root if root is not None else REPO_ROOT / "tests"
    if not scan_root.is_dir():
        raise NotADirectoryError(f"not a directory: {scan_root}")
    found: list[MockOnlyTest] = []
    for path in sorted(scan_root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for func in _iter_test_functions(tree=tree):
            if _is_mock_only(func=func):
                found.append(
                    MockOnlyTest(
                        path=_display_path(path=path),
                        name=func.name,
                        lineno=func.lineno,
                    ),
                )
    return sorted(found, key=lambda t: (t.path, t.lineno))


def main(argv: list[str] | None = None) -> int:
    """Print the mock-only tests and exit non-zero when any exist.

    Args:
        argv: Command-line arguments, defaulting to ``sys.argv[1:]``.

    Returns:
        ``0`` when no mock-only test was found, ``1`` when at least one
        exists, and ``2`` when ``--root`` does not name a directory.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Directory to scan (default: the repository tests/ tree).",
    )
    args = parser.parse_args(argv)
    try:
        offenders = find_mock_only_tests(root=args.root)
    except NotADirectoryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for offender in offenders:
        print(f"{offender.path}:{offender.lineno} {offender.name}")
    print(f"{len(offenders)} mock-only test(s)")
    return 1 if offenders else 0


if __name__ == "__main__":
    sys.exit(main())

"""No test module may reconfigure loguru at import time (#2375).

``logger.add`` and ``logger.remove`` mutate a process-global handler table.
Done at module scope they run during collection, before any fixture can
snapshot the state, so the change outlives every test in the session and the
autouse isolation fixture in ``tests/conftest.py`` never sees it.

That is not theoretical: three integration modules installed
``logger.add(lambda msg: print(msg, end=""), level="INFO")`` at import. ``print``
resolves ``sys.stdout`` when it is called, so once a ``CliRunner`` invocation
was in progress the sink wrote loguru's default-format line straight into the
captured output, ahead of the JSON envelope the CLI tests parse — and the
Python-compat matrix failed with ``JSONDecodeError: Extra data`` on whichever
randomised orders left that sink installed.
"""

from __future__ import annotations

import ast
from pathlib import Path

from assertpy import assert_that

TESTS_ROOT = Path(__file__).resolve().parents[1]

#: Loguru configuration calls that must not run at import time.
_LOGURU_CONFIG_METHODS = frozenset({"add", "remove", "configure"})

#: Names test modules bind the loguru logger to.
_LOGURU_NAMES = frozenset({"logger", "loguru_logger"})


def _is_loguru_config_call(call: ast.Call) -> bool:
    """Report whether a call targets a loguru handler-table method.

    Args:
        call: Call expression to inspect.

    Returns:
        ``True`` for ``logger.add``/``remove``/``configure`` and the aliases
        test modules import loguru under.
    """
    func = call.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr in _LOGURU_CONFIG_METHODS
        and isinstance(func.value, ast.Name)
        and func.value.id in _LOGURU_NAMES
    )


def _import_time_calls(node: ast.AST) -> list[ast.Call]:
    """Collect the calls that run when a module is imported.

    ``ast.walk`` is flat, so it cannot skip a whole function body; this walks
    children explicitly and prunes every callable definition, which is the
    supported place to add and remove a sink.

    Args:
        node: Node whose import-time descendants to collect.

    Returns:
        Every call expression that executes at import time.
    """
    calls: list[ast.Call] = []
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            continue
        if isinstance(child, ast.Call):
            calls.append(child)
        calls.extend(_import_time_calls(child))
    return calls


def _module_level_loguru_calls(*, path: Path) -> list[str]:
    """Find import-time loguru configuration in one module.

    Args:
        path: Python file to scan.

    Returns:
        One ``"<path>:<line>"`` entry per offending call.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return []
    try:
        shown: Path = path.relative_to(TESTS_ROOT.parent)
    except ValueError:
        shown = path
    return [
        f"{shown}:{call.lineno}"
        for call in _import_time_calls(tree)
        if _is_loguru_config_call(call)
    ]


def test_no_test_module_configures_loguru_at_import_time() -> None:
    """Loguru stays untouched until a fixture can snapshot and restore it."""
    offenders = sorted(
        entry
        for path in TESTS_ROOT.rglob("*.py")
        for entry in _module_level_loguru_calls(path=path)
    )

    assert_that(offenders).is_empty()


def test_the_scanner_flags_an_import_time_sink(tmp_path: Path) -> None:
    """A module that adds a sink at import is reported.

    Without this the ratchet above could pass by finding nothing at all.

    Args:
        tmp_path: Pytest temporary directory holding the planted module.
    """
    planted = tmp_path / "test_planted.py"
    planted.write_text(
        'from loguru import logger\n\nlogger.add(print, level="INFO")\n',
        encoding="utf-8",
    )

    offenders = _module_level_loguru_calls(path=planted)

    assert_that(offenders).is_length(1)
    assert_that(offenders[0]).ends_with(":3")


def test_the_scanner_allows_a_sink_added_inside_a_fixture(tmp_path: Path) -> None:
    """A sink added inside a function is left alone.

    Args:
        tmp_path: Pytest temporary directory holding the planted module.
    """
    planted = tmp_path / "test_planted_fixture.py"
    planted.write_text(
        "from loguru import logger\n"
        "\n"
        "\n"
        "def sink_fixture() -> None:\n"
        '    sink_id = logger.add(print, level="INFO")\n'
        "    logger.remove(sink_id)\n",
        encoding="utf-8",
    )

    assert_that(_module_level_loguru_calls(path=planted)).is_empty()

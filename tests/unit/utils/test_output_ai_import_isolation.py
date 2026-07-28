"""Pin the core output package as free of :mod:`lintro.ai` imports.

Issue #724: importing ``lintro.utils.output`` used to drag in 19 ``lintro.ai``
modules with AI fully disabled, because ``lintro.utils.json_output`` imported
``lintro.ai.metadata`` at module scope. The count must stay at zero, so this
runs in a fresh interpreter — the in-process ``sys.modules`` is polluted by
other tests that legitimately import the AI layer.
"""

from __future__ import annotations

import subprocess  # nosec B404 - runs a fixed argv against this interpreter
import sys

from assertpy import assert_that

_COUNT_SNIPPET = (
    "import sys, lintro.utils.output; "
    "print(len([m for m in sys.modules if m.startswith('lintro.ai')]))"
)

_NAMES_SNIPPET = (
    "import sys, {module}; "
    "print(','.join(sorted(m for m in sys.modules "
    "if m.startswith('lintro.ai'))))"
)


def _loaded_ai_modules(module: str) -> list[str]:
    """Import ``module`` in a fresh interpreter and list loaded AI modules.

    Args:
        module: Dotted module path to import.

    Returns:
        Sorted names of the ``lintro.ai`` modules present in ``sys.modules``.
    """
    completed = subprocess.run(  # nosec B603 - fixed argv, shell=False, no user input
        [sys.executable, "-c", _NAMES_SNIPPET.format(module=module)],
        capture_output=True,
        text=True,
        check=True,
    )
    return [name for name in completed.stdout.strip().split(",") if name]


def test_importing_output_package_loads_no_ai_modules() -> None:
    """``import lintro.utils.output`` must not load any AI module."""
    assert_that(_loaded_ai_modules("lintro.utils.output")).is_empty()


def test_importing_json_output_loads_no_ai_modules() -> None:
    """The JSON serializer is the edge #724 removed; keep it clean."""
    assert_that(_loaded_ai_modules("lintro.utils.json_output")).is_empty()


def test_importing_tool_executor_loads_no_ai_modules() -> None:
    """The core runner reaches AI only through injected seams."""
    assert_that(_loaded_ai_modules("lintro.utils.tool_executor")).is_empty()


def test_module_count_snippet_reports_zero() -> None:
    """The exact invariant quoted in issue #724 reads zero."""
    completed = subprocess.run(  # nosec B603 - fixed argv, shell=False, no user input
        [sys.executable, "-c", _COUNT_SNIPPET],
        capture_output=True,
        text=True,
        check=True,
    )

    assert_that(completed.stdout.strip()).is_equal_to("0")

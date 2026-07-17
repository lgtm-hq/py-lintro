"""Public library API for programmatic lintro invocation.

Import these functions to embed lintro in another Python program::

    from lintro.api import check, fmt, test

    result = check(paths=["src"], tools="ruff")
    if not result.success:
        ...

Unlike the CLI entry points, these functions return a structured
:class:`~lintro.api.core.LintroResult` and let exceptions propagate to the
caller instead of swallowing them.
"""

from lintro.api.core import (
    LintroResult,
    check,
    fmt,
    format,
    test,
)

__all__ = [
    "LintroResult",
    "check",
    "fmt",
    "format",
    "test",
]

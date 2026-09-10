"""Public library API for programmatic lintro invocation.

Import these functions to embed lintro in another Python program::

    from lintro.api import check, fmt, test

    result = check(paths=["src"], tools="ruff")
    if not result.success:
        ...

Unlike the CLI entry points, these functions return a structured
:class:`~lintro.api.core.LintroResult` and let exceptions propagate to the
caller instead of swallowing them.

The ``*_run`` variants return the full
:class:`~lintro.models.core.run_artifact.RunArtifact` instead of an exit code,
so callers can inspect per-tool results, totals, and severity tallies without
re-parsing lintro's own output (issue #1823)::

    from lintro.api import check_run

    artifact = check_run(paths=["src"], tools="ruff")
    for result in artifact.tool_results:
        ...
"""

from lintro.api.core import (
    LintroResult,
    check,
    check_run,
    fmt,
    fmt_run,
    format,
    format_run,
    test,
    test_run,
)

__all__ = [
    "LintroResult",
    "check",
    "check_run",
    "fmt",
    "fmt_run",
    "format",
    "format_run",
    "test",
    "test_run",
]

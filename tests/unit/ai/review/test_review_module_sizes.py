"""Ratchets on module shape inside ``lintro/ai/review`` (issue #2301).

Acceptance criterion 1 of #2301 is that no module directly in
``lintro/ai/review/`` is longer than 500 lines, and #2301's slices split every
module that was. The
repo-wide module-size gate warns at 800 lines, so it cannot hold this line —
a 600-line review module would pass it and quietly undo the split.

The GitHub-surface modules (``github.py`` and ``github_*.py``) are the one
exception: they are owned by #1974, which splits them separately, so they are
listed here by name rather than matched by a prefix. A new module in the
package is covered by the ratchet from the moment it is added, and shrinking
#1974's list as those modules land needs no change here.

Scope is the ``lintro/ai/review`` package directory itself, which is what
#2301's slices enumerated. Its ``chunker/`` and ``context/`` subpackages are
part of the wider >500-line burn-down owned by #1995 and are not ratcheted
here; extend this to ``rglob`` when that issue lands.

The second ratchet is #2301's other structural criterion: no function in the
package takes more than 8 parameters. Ruff's ``PLR0913`` enforces that repo-wide
with a per-file baseline, and the closing slice emptied that baseline for this
package by bundling the last three fat signatures into frozen ``kw_only``
request dataclasses. Asserting it here as well means a re-grown signature fails
on its own terms rather than only as a new baseline entry someone might add.
"""

from __future__ import annotations

import ast
from pathlib import Path

from assertpy import assert_that

from lintro.utils.module_size import count_module_lines

#: Longest a review module may be. Never raise this.
MAX_REVIEW_MODULE_LINES: int = 500

#: Most parameters a function in the package may take, mirroring ruff's
#: ``PLR0913`` threshold and ``MAX_ALLOWED_ARGS`` in the structural-baseline
#: test. Never raise this either.
MAX_REVIEW_FUNCTION_PARAMETERS: int = 8

REVIEW_PACKAGE = Path(__file__).resolve().parents[4] / "lintro" / "ai" / "review"

#: GitHub-surface modules split by #1974, not by #2301. Entries leave this set
#: as that issue lands; nothing is ever added to it.
GITHUB_SURFACE_MODULES: frozenset[str] = frozenset(
    {
        "github.py",
        "github_errors.py",
        "github_lifecycle.py",
        "github_render.py",
        "github_review_body.py",
        "github_sticky.py",
    },
)


def _module_line_counts() -> dict[str, int]:
    """Count physical lines for every module under the review package.

    Returns:
        dict[str, int]: Repo-relative POSIX path to physical line count, for
        every module directly in ``lintro/ai/review``.
    """
    root = REVIEW_PACKAGE.parents[2]
    return {
        path.relative_to(root).as_posix(): count_module_lines(file_path=str(path))
        for path in sorted(REVIEW_PACKAGE.glob("*.py"))
    }


def test_no_review_module_exceeds_the_size_ratchet() -> None:
    """Every non-GitHub review module stays at or under 500 lines."""
    counts = _module_line_counts()
    oversized = {
        path: lines
        for path, lines in counts.items()
        if lines > MAX_REVIEW_MODULE_LINES
        and Path(path).name not in GITHUB_SURFACE_MODULES
    }

    # A mistyped package path would otherwise measure nothing and pass.
    assert_that(counts).is_not_empty()
    assert_that(oversized).is_empty()


def test_github_surface_exceptions_all_exist() -> None:
    """Every named #1974 exception is still a module in the package.

    A stale name here would silently widen the exemption to a file that no
    longer exists while a same-named new module inherited the pass.
    """
    present = {path.name for path in REVIEW_PACKAGE.glob("*.py")}

    assert_that(GITHUB_SURFACE_MODULES.issubset(present)).is_true()


def _parameter_count(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """Count a function's declared parameters the way ``PLR0913`` does.

    ``self`` and ``cls`` do not count, and neither do ``*args`` / ``**kwargs``:
    the rule is about how many named values a caller has to line up.

    Args:
        node: The function definition to measure.

    Returns:
        int: Number of positional, positional-only and keyword-only
        parameters, excluding an instance or class receiver.
    """
    arguments = node.args
    names = [
        argument.arg
        for argument in (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs)
    ]
    if names and names[0] in {"self", "cls"}:
        names = names[1:]
    return len(names)


def test_no_review_function_exceeds_the_parameter_ratchet() -> None:
    """No function in the package takes more than 8 parameters.

    #2301's acceptance criterion, held as a test rather than as the absence of
    a ruff baseline entry. The GitHub-surface modules are #1974's, and still
    carry their ``PLR0913`` entries.
    """
    offenders: dict[str, int] = {}
    for path in sorted(REVIEW_PACKAGE.glob("*.py")):
        if path.name in GITHUB_SURFACE_MODULES:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            count = _parameter_count(node)
            if count > MAX_REVIEW_FUNCTION_PARAMETERS:
                offenders[f"{path.name}::{node.name}"] = count

    assert_that(offenders).is_empty()

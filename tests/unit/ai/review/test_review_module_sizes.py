"""Ratchets on module shape inside ``lintro/ai/review`` (issue #2301).

Acceptance criterion 1 of #2301 is that no module directly in
``lintro/ai/review/`` is longer than 500 lines, and #2301's slices split every
module that was. The
repo-wide module-size gate warns at 800 lines, so it cannot hold this line —
a 600-line review module would pass it and quietly undo the split.

The GitHub-surface modules were the one exception, listed by name while epic
#1974 split them on its own schedule. #2305 was the last of those slices, so
:data:`GITHUB_SURFACE_MODULES` is now empty and the ratchet covers every
module in the package without exception. The set stays here, empty, because
its emptiness is the assertion: a future issue must not be able to re-open the
exemption by quietly adding a name back.

Scope is the ``lintro/ai/review`` package directory itself, which is what
#2301's slices enumerated, plus the ``sticky/`` subpackage #2304 split
``github_sticky.py`` into and the ``lifecycle/`` subpackage #2305 split
``github_lifecycle.py`` into — a split that moved lines out of the glob would
otherwise have bought the ratchet's silence rather than passing it. The
``chunker/`` and ``context/`` subpackages are part of the wider >500-line
burn-down owned by #1995 and are not ratcheted here; extend this to ``rglob``
when that issue lands.

The second ratchet is #2301's other structural criterion: no function in the
package takes more than 8 parameters. Ruff's ``PLR0913`` enforces that repo-wide
with a per-file baseline, and the closing slice emptied that baseline for this
package by bundling the last fat signatures into frozen ``kw_only`` request
dataclasses. Asserting it here as well means a re-grown signature fails on its
own terms rather than only as a new baseline entry someone might add.
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

#: Subpackages ratcheted alongside the package directory itself. ``sticky/``
#: is #2304's split of ``github_sticky.py`` and ``lifecycle/`` is #2305's split
#: of ``github_lifecycle.py``; entries are added here whenever a module in
#: scope is split into a package rather than sibling modules.
RATCHETED_SUBPACKAGES: tuple[str, ...] = ("lifecycle", "sticky")

#: GitHub-surface modules #1974 split rather than #2301. Empty since #2305
#: landed the last slice: every module in the package is now ratcheted.
#: Entries may only ever leave this set.
GITHUB_SURFACE_MODULES: frozenset[str] = frozenset()


def _ratcheted_modules() -> list[Path]:
    """List every module the ratchets cover.

    Returns:
        list[Path]: Modules directly in ``lintro/ai/review`` plus those in the
        subpackages named by :data:`RATCHETED_SUBPACKAGES`.

    Raises:
        AssertionError: When a named subpackage no longer exists. A stale name
            would silently measure nothing instead of failing.
    """
    paths = list(REVIEW_PACKAGE.glob("*.py"))
    for package in RATCHETED_SUBPACKAGES:
        package_path = REVIEW_PACKAGE / package
        if not package_path.is_dir():
            msg = f"ratcheted subpackage is missing: {package_path}"
            raise AssertionError(msg)
        paths.extend(package_path.glob("*.py"))
    return sorted(paths)


def _module_line_counts() -> dict[str, int]:
    """Count physical lines for every module under the review package.

    Returns:
        dict[str, int]: Repo-relative POSIX path to physical line count, for
        every module directly in ``lintro/ai/review``.
    """
    root = REVIEW_PACKAGE.parents[2]
    return {
        path.relative_to(root).as_posix(): count_module_lines(file_path=str(path))
        for path in _ratcheted_modules()
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


def test_the_github_surface_exemption_is_closed() -> None:
    """#1974's exemption is spent, so the ratchet covers the whole package.

    Held as its own assertion rather than left implicit in an empty set: the
    two other tests skip whatever this names, so re-adding a name here is the
    one edit that can widen the ratchet's blind spot without failing anything.
    """
    assert_that(GITHUB_SURFACE_MODULES).is_empty()


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
    a ruff baseline entry.
    """
    offenders: dict[str, int] = {}
    for path in _ratcheted_modules():
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

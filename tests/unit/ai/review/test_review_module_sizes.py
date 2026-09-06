"""Ratchet on module size inside ``lintro/ai/review`` (issue #2301).

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
"""

from __future__ import annotations

from pathlib import Path

from assertpy import assert_that

from lintro.utils.module_size import count_module_lines

#: Longest a review module may be. Never raise this.
MAX_REVIEW_MODULE_LINES: int = 500

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

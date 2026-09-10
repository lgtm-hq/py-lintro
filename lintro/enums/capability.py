"""Capability enum describing what a tool does to the files it claims.

A tool's capabilities say *how* it touches a file, which is the data the
derived scheduler (#1735) needs in place of the scalar ``priority`` integer.
Three capabilities are enough to describe every tool lintro wraps:

- :attr:`Cap.FIX` — rewrites a file to remove diagnostics.
- :attr:`Cap.FORMAT` — rewrites a file to a canonical layout.
- :attr:`Cap.CHECK` — reports diagnostics without rewriting anything.

``LINT`` and ``ANALYZE`` are deliberately absent. Both would have been
synonyms for :attr:`Cap.CHECK` distinguished only by how deep the tool looks,
which is not a scheduling input: mypy is a project-scoped ``CHECK`` and
html-validate is a per-file ``CHECK``, and the ordering consequence of that
difference is carried by ``partitionable``, not by a capability.
"""

from __future__ import annotations

from enum import StrEnum, auto


class Cap(StrEnum):
    """What a tool does to a file it claims.

    Attributes:
        FIX: Rewrites the file to remove diagnostics (e.g. ``ruff check
            --fix``). Runs before :attr:`FORMAT` because a fix rewrites
            structure and leaves layout dirty.
        FORMAT: Rewrites the file to a canonical layout (e.g. ``black``).
            At most one tool may hold ``FORMAT`` for a given pattern.
        CHECK: Reports diagnostics only; never mutates the file.
    """

    FIX = auto()
    FORMAT = auto()
    CHECK = auto()


#: Capabilities that mutate files on disk.
MUTATING_CAPABILITIES: frozenset[Cap] = frozenset({Cap.FIX, Cap.FORMAT})

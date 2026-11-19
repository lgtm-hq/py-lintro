"""Shape of a dependency spec declared in a project's ``package.json``."""

from __future__ import annotations

from enum import StrEnum, auto


class DeclaredSpecKind(StrEnum):
    """How lintro treats a declared ``package.json`` dependency spec (#2045).

    The class is decided by the spec's prefix and shape alone. lintro does not
    resolve semver ranges: when a range is declared, the project's own package
    manager does the resolving.
    """

    #: A full version with at most one ``^``, ``~`` or ``=`` operator and at
    #: most one ``v``, for example ``^3.9.4`` or ``=v3.9.4``. Build metadata
    #: is ignored. Compared with lintro's recommendation.
    EXACT = auto()
    #: A range such as ``>=3.9.4``, ``3.9.x``, ``*`` or ``1.x || 2.x``. The
    #: project's range governs when it can reach lintro's minimum; a range
    #: whose upper bound is below it is reported instead of installed.
    RANGE = auto()
    #: A spec that is not a version or a range, for example ``workspace:*``,
    #: an ``npm:`` alias, a git or tarball URL, a local path or a dist-tag.
    #: lintro never overrides it.
    OPAQUE = auto()

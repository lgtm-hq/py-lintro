"""Shared manifest and lockfile name vocabulary for AI review (issue #1973).

The chunker's local-action resolver and the changed-file classifier both had to
know which filenames are package manifests or dependency lockfiles, and each
kept its own literal set. The two sets drifted: ``yarn.lock`` was recognised by
the action resolver but not by the classifier, and neither knew about the
lockfiles that modern Python and Rust toolchains emit.

This module holds the one shared core both consumers derive from, plus the
extras that only the classifier's wider, ecosystem-agnostic scope needs. Adding
a name here is the only place a new manifest has to be registered; which
consumer picks it up is decided by which set it is added to.
"""

from __future__ import annotations

#: Manifests and lockfiles recognised by every consumer of this vocabulary.
#:
#: These are the JavaScript package manifests and lockfiles that both define a
#: local GitHub Action's runtime dependencies or entrypoint and count as a
#: dependency change in their own right, so both consumers want all of them.
SHARED_MANIFEST_NAMES: frozenset[str] = frozenset(
    {
        "bun.lock",
        "bun.lockb",
        "package-lock.json",
        "package.json",
        "pnpm-lock.yaml",
        "yarn.lock",
    },
)

#: Dependency manifests and lockfiles outside the shared core.
#:
#: These belong to the classifier's ecosystem-agnostic scope only. The action
#: resolver deliberately keeps the narrower core: it only walks up from files
#: that sit inside a ``.github/actions/`` directory, where the JavaScript
#: manifests above are the ones that appear in practice. ``npm-shrinkwrap.json``
#: is JavaScript but stays here for the same reason - #1973 reconciles the two
#: vocabularies without widening what the resolver matches.
DEPENDENCY_MANIFEST_EXTRA_NAMES: frozenset[str] = frozenset(
    {
        "cargo.lock",
        "cargo.toml",
        "composer.json",
        "go.mod",
        "npm-shrinkwrap.json",
        "poetry.lock",
        "pyproject.toml",
        "uv.lock",
    },
)

"""Provenance of a selected Node.js package manager."""

from __future__ import annotations

from enum import StrEnum, auto


class NodeManagerSource(StrEnum):
    """Why a particular Node.js package manager was selected.

    The members are declared in decreasing authority, which is the order the
    selection policy consults them (see
    :func:`lintro.tools.core.install_strategies.node_project.select_node_manager`).
    Reporting the source alongside the manager matters because "bun, because it
    happened to be on PATH" and "bun, because package.json says so" warrant very
    different levels of user trust.
    """

    #: The user named the manager explicitly (CLI flag).
    EXPLICIT = auto()
    #: The project's ``package.json`` carries a ``packageManager`` field.
    PACKAGE_MANAGER_FIELD = auto()
    #: A lockfile in the project root identifies the manager.
    LOCKFILE = auto()
    #: Nothing in the project said; fell back to what is installed.
    AVAILABLE_FALLBACK = auto()

"""Shared Cargo workspace discovery for Rust tool definitions.

``cargo clippy``, ``cargo fmt`` and ``cargo deny`` all have to be invoked from a
directory that owns a ``Cargo.toml``; the paths lintro hands a plugin are
whatever its file patterns matched instead. Every Rust definition therefore
walked each path upward to the nearest manifest and reconciled the results into
a single working directory. This module holds that walk once.

Example:
    >>> from lintro.tools.core.cargo import find_cargo_root
    >>> find_cargo_root(["src/main.rs"])  # doctest: +SKIP
    PosixPath('/repo')
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from loguru import logger

__all__ = ["CARGO_MANIFEST", "find_cargo_root"]

#: The manifest file that marks a Cargo package or workspace root.
CARGO_MANIFEST: str = "Cargo.toml"

#: Directory marker that ends the upward walk, so discovery cannot escape the
#: repository into an unrelated manifest further up the filesystem.
_REPOSITORY_MARKER: str = ".git"


def _declares_workspace(manifest: Path) -> bool:
    """Report whether a manifest declares a ``[workspace]`` table.

    Args:
        manifest: Path to a ``Cargo.toml`` file.

    Returns:
        ``True`` when the manifest parses and carries a top-level
        ``workspace`` table, ``False`` otherwise (an unreadable or malformed
        manifest is treated as "not a workspace" rather than raising).
    """
    try:
        with manifest.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        logger.debug("Could not read Cargo manifest {}: {}", manifest, exc)
        return False
    return isinstance(data.get("workspace"), dict)


def _repository_ancestors(start: Path) -> list[Path]:
    """Collect every ancestor of ``start`` that holds ``.git``.

    Args:
        start: Directory to begin the upward walk at, inclusive.

    Returns:
        The repositories containing ``start``, nearest first. A nested
        checkout or submodule contributes its own entry ahead of the outer
        repository's.
    """
    return [
        candidate
        for candidate in [start, *start.parents]
        if (candidate / _REPOSITORY_MARKER).exists()
    ]


def _repository_boundary(roots: set[Path]) -> tuple[Path | None, bool]:
    """Find the outermost repository that contains every root.

    A workspace member may be a repository of its own — a submodule or a
    nested checkout — so the boundary is the shallowest repository shared by
    all of the roots rather than each root's nearest one.

    Args:
        roots: Package directories the walk has to stay inside of.

    Returns:
        A ``(boundary, split)`` pair. ``boundary`` is the shallowest
        repository containing every root, or ``None`` when no repository
        does. ``split`` is ``True`` when at least one root lives in a
        repository that does not contain the others, which makes any manifest
        above them all unrelated to the inputs.
    """
    chains = [_repository_ancestors(root) for root in roots]
    shared: set[Path] = set(chains[0])
    for chain in chains[1:]:
        shared &= set(chain)
    if shared:
        return min(shared, key=lambda path: len(path.parts)), False
    return None, any(chains)


def _nearest_workspace_root(start: Path, boundary: Path | None) -> Path | None:
    """Walk upward from ``start`` to the first workspace manifest.

    Args:
        start: Directory to begin the upward walk at, inclusive.
        boundary: Outermost repository the walk may reach, inclusive. Only
            this directory ends the walk, so a nested repository between
            ``start`` and ``boundary`` is walked through. ``None`` leaves the
            walk unbounded, for inputs that live outside any repository.

    Returns:
        The directory owning the nearest ``Cargo.toml`` with a
        ``[workspace]`` table, or ``None`` when the walk reaches the boundary
        or the filesystem root without finding one.
    """
    for candidate in [start, *start.parents]:
        manifest = candidate / CARGO_MANIFEST
        if manifest.is_file() and _declares_workspace(manifest):
            return candidate
        if candidate == boundary:
            break
    return None


def _nearest_manifest_dirs(paths: list[str]) -> list[Path]:
    """Collect the nearest manifest-owning directory for each input path.

    Args:
        paths: File or directory paths to search upward from.

    Returns:
        One directory per path that has a manifest above it, in input order.
    """
    roots: list[Path] = []
    for raw_path in paths:
        current = Path(raw_path).resolve()
        if current.is_file():
            current = current.parent
        for candidate in [current, *current.parents]:
            if (candidate / CARGO_MANIFEST).exists():
                roots.append(candidate)
                break
    return roots


def find_cargo_root(
    paths: list[str],
    *,
    tool_label: str | None = None,
) -> Path | None:
    """Return the directory a Cargo command should run from.

    Each path is walked upward to the nearest ``Cargo.toml``. When the paths
    resolve to a single package that package's directory is returned. When they
    straddle several packages the walk continues upward from their common
    ancestor until a manifest declaring a ``[workspace]`` table is found, so a
    nested member set resolves to the workspace root rather than to one of its
    members. An ancestor manifest that declares only ``[package]`` is rejected:
    running Cargo there would act on that crate alone, not on the packages the
    files belong to. The walk stops at the outermost repository containing
    every path, so it cannot escape into an unrelated manifest while still
    crossing a member that is a repository of its own; paths that no single
    repository contains — sibling repositories, or a repository mixed with a
    tree outside one — resolve to nothing rather than to a manifest above
    them all.

    Args:
        paths: File or directory paths to search upward from.
        tool_label: Tool name used to explain an unresolvable multi-root
            layout to the user. When ``None`` the failure is silent.

    Returns:
        The Cargo root to use, or ``None`` when no usable root exists.
    """
    roots = _nearest_manifest_dirs(paths)
    if not roots:
        return None

    unique_roots = set(roots)
    if len(unique_roots) == 1:
        return roots[0]

    try:
        common = Path(os.path.commonpath([str(root) for root in unique_roots]))
    except ValueError:
        if tool_label is not None:
            logger.warning(
                "Multiple Cargo roots found on different drives; cannot determine "
                "common workspace root. Skipping {}.",
                tool_label,
            )
        return None

    boundary, split = _repository_boundary(unique_roots)
    if split:
        if tool_label is not None:
            logger.warning(
                "Multiple Cargo roots found ({}) in different repositories; "
                "any workspace manifest above them all is unrelated. Skipping "
                "{}.",
                ", ".join(str(root) for root in sorted(unique_roots)),
                tool_label,
            )
        return None

    workspace_root = _nearest_workspace_root(common, boundary)
    if workspace_root is not None:
        return workspace_root

    if tool_label is not None:
        logger.warning(
            "Multiple Cargo roots found ({}) without a common workspace "
            "Cargo.toml. Consider creating a workspace or running {} on each "
            "crate separately.",
            ", ".join(str(root) for root in unique_roots),
            tool_label,
        )
    return None

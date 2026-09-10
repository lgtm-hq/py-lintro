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
    return "workspace" in data


def _nearest_workspace_root(start: Path) -> Path | None:
    """Walk upward from ``start`` to the first workspace manifest.

    Args:
        start: Directory to begin the upward walk at, inclusive.

    Returns:
        The directory owning the nearest ``Cargo.toml`` with a
        ``[workspace]`` table, or ``None`` when the walk reaches a repository
        boundary or the filesystem root without finding one.
    """
    for candidate in [start, *start.parents]:
        manifest = candidate / CARGO_MANIFEST
        if manifest.is_file() and _declares_workspace(manifest):
            return candidate
        if (candidate / _REPOSITORY_MARKER).exists():
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
    files belong to. The walk stops at a directory holding ``.git`` so it
    cannot escape the repository.

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

    workspace_root = _nearest_workspace_root(common)
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

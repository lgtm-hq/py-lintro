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
from pathlib import Path

from loguru import logger

__all__ = ["CARGO_MANIFEST", "find_cargo_root"]

#: The manifest file that marks a Cargo package or workspace root.
CARGO_MANIFEST: str = "Cargo.toml"


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
    resolve to a single package that package's directory is returned; when they
    straddle several packages the common ancestor is used, but only if it owns
    a ``Cargo.toml`` of its own. The manifest's contents are not read, so an
    ancestor package works as well as an explicit ``[workspace]``.

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

    if (common / CARGO_MANIFEST).exists():
        return common

    if tool_label is not None:
        logger.warning(
            "Multiple Cargo roots found ({}) without a common workspace "
            "Cargo.toml. Consider creating a workspace or running {} on each "
            "crate separately.",
            ", ".join(str(root) for root in unique_roots),
            tool_label,
        )
    return None

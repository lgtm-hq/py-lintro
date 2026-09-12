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
from dataclasses import dataclass
from enum import StrEnum, auto
from pathlib import Path
from typing import Any

from loguru import logger

__all__ = [
    "CARGO_MANIFEST",
    "CargoRoot",
    "CargoRootIssue",
    "cargo_package_args",
    "find_cargo_root",
    "resolve_cargo_root",
]

#: The manifest file that marks a Cargo package or workspace root.
CARGO_MANIFEST: str = "Cargo.toml"

#: Directory marker that ends the upward walk, so discovery cannot escape the
#: repository into an unrelated manifest further up the filesystem.
_REPOSITORY_MARKER: str = ".git"

#: Manifest tables whose entries may carry a ``path`` dependency.
_DEPENDENCY_TABLES: tuple[str, ...] = (
    "dependencies",
    "dev-dependencies",
    "build-dependencies",
)


class CargoRootIssue(StrEnum):
    """Reason a set of paths has no directory a Cargo command can run from."""

    #: No path has a ``Cargo.toml`` above it at all.
    NO_MANIFEST = auto()
    #: The roots sit on drives with no shared ancestor.
    SEPARATE_DRIVES = auto()
    #: The roots sit in repositories that do not contain one another.
    SPLIT_REPOSITORIES = auto()
    #: The only manifest above the roots declares ``[package]`` alone.
    PACKAGE_ONLY_ANCESTOR = auto()
    #: The walk reached the repository boundary without an owning workspace.
    REPOSITORY_BOUNDARY = auto()
    #: No ``[workspace]`` manifest anywhere above the roots owns them all.
    NO_OWNING_WORKSPACE = auto()


#: User-facing explanation per rejection reason, formatted with the tool name.
_SKIP_MESSAGES: dict[CargoRootIssue, str] = {
    CargoRootIssue.NO_MANIFEST: "No Cargo.toml found; skipping {tool}.",
    CargoRootIssue.SEPARATE_DRIVES: (
        "Cargo roots span separate drives, so they share no workspace root; "
        "skipping {tool}."
    ),
    CargoRootIssue.SPLIT_REPOSITORIES: (
        "Cargo roots lie in separate repositories, so no manifest above them "
        "all is related to them; skipping {tool}."
    ),
    CargoRootIssue.PACKAGE_ONLY_ANCESTOR: (
        "The nearest shared Cargo.toml declares only a package, not a "
        "workspace, so running there would cover one crate; skipping {tool}."
    ),
    CargoRootIssue.REPOSITORY_BOUNDARY: (
        "No workspace Cargo.toml inside the repository owns every Cargo root; "
        "skipping {tool}."
    ),
    CargoRootIssue.NO_OWNING_WORKSPACE: (
        "No workspace Cargo.toml owns every Cargo root; skipping {tool}."
    ),
}


@dataclass(frozen=True)
class CargoRoot:
    """The outcome of resolving a Cargo working directory.

    Attributes:
        root: The directory a Cargo command should run from, or ``None``
            when the paths resolve to no usable root.
        issue: Why no root was found. ``None`` exactly when ``root`` is set.
    """

    root: Path | None
    issue: CargoRootIssue | None

    def skip_message(self, tool_label: str) -> str:
        """Explain the rejection in the tool's own skip output.

        Args:
            tool_label: Tool name to name in the message.

        Returns:
            A sentence naming the reason no Cargo root was usable. Falls back
            to the generic wording when there is no recorded reason, which
            only happens when a root was found.
        """
        if self.issue is None:
            return f"No Cargo.toml found; skipping {tool_label}."
        return _SKIP_MESSAGES[self.issue].format(tool=tool_label)


def _read_manifest(manifest: Path) -> dict[str, Any] | None:
    """Parse a ``Cargo.toml`` file.

    Args:
        manifest: Path to a ``Cargo.toml`` file.

    Returns:
        The parsed manifest, or ``None`` when it cannot be read or parsed. An
        unreadable or malformed manifest is treated as "not a workspace"
        rather than raising.
    """
    try:
        with manifest.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        logger.debug("Could not read Cargo manifest {}: {}", manifest, exc)
        return None


def _resolve_patterns(base: Path, patterns: Any) -> set[Path]:
    """Resolve ``members`` or ``exclude`` entries to directories.

    Args:
        base: Directory owning the manifest the patterns came from.
        patterns: The raw TOML value, expected to be a list of relative path
            strings. Anything else contributes nothing.

    Returns:
        The directories the patterns name. An absolute entry stays absolute,
        the way Cargo reads it; a relative one is joined onto ``base``.
        Entries holding a glob character are expanded against whichever of
        the two they are anchored to. A pattern the platform cannot expand —
        one escaping ``base`` with ``..``, say — contributes no matches
        instead of raising.
    """
    resolved: set[Path] = set()
    if not isinstance(patterns, list):
        return resolved
    for pattern in patterns:
        if not isinstance(pattern, str):
            continue
        cleaned = pattern.rstrip("/")
        if not cleaned:
            continue
        candidate = Path(cleaned)
        anchor = Path(candidate.anchor) if candidate.is_absolute() else base
        relative = (
            str(candidate.relative_to(candidate.anchor))
            if candidate.is_absolute()
            else cleaned
        )
        if not relative or relative == ".":
            continue
        if any(character in relative for character in "*?["):
            try:
                matches = list(anchor.glob(relative))
            except (ValueError, OSError, NotImplementedError) as exc:
                logger.debug("Unusable Cargo member pattern {!r}: {}", pattern, exc)
                continue
            resolved.update(match for match in matches if match.is_dir())
        else:
            resolved.add(anchor / relative)
    return {path.resolve() for path in resolved}


def _is_within(path: Path, directory: Path) -> bool:
    """Report whether ``path`` is ``directory`` or sits below it.

    Args:
        path: Directory to test.
        directory: Directory that may contain ``path``.

    Returns:
        ``True`` when ``path`` is inside ``directory``, inclusive.
    """
    return path == directory or directory in path.parents


def _dependency_tables(container: dict[str, Any]) -> list[Any]:
    """Collect a table's dependency tables, plain and target-specific.

    Args:
        container: A parsed manifest, or its ``[workspace]`` table, which
            carries the same dependency and ``target`` keys.

    Returns:
        The raw values of every dependency table found, unvalidated.
    """
    tables: list[Any] = [container.get(name) for name in _DEPENDENCY_TABLES]
    targets = container.get("target")
    if isinstance(targets, dict):
        for target in targets.values():
            if isinstance(target, dict):
                tables.extend(target.get(name) for name in _DEPENDENCY_TABLES)
    return tables


def _path_dependencies(manifest_dir: Path, data: dict[str, Any]) -> set[Path]:
    """Collect the directories a manifest's ``path`` dependencies point at.

    Args:
        manifest_dir: Directory owning the manifest.
        data: The parsed manifest.

    Returns:
        The resolved directories named by ``path`` entries in the plain and
        target-specific dependency tables, and in the ``[workspace]`` copies
        of both. A member that inherits a dependency with ``workspace = true``
        keeps the path only in ``[workspace.dependencies]``, so that table
        counts towards membership too.
    """
    tables: list[Any] = list(_dependency_tables(data))
    workspace = data.get("workspace")
    if isinstance(workspace, dict):
        tables.extend(_dependency_tables(workspace))
    directories: set[Path] = set()
    for table in tables:
        if not isinstance(table, dict):
            continue
        for spec in table.values():
            location = spec.get("path") if isinstance(spec, dict) else None
            if isinstance(location, str) and location:
                directories.add((manifest_dir / location).resolve())
    return directories


def _with_path_dependencies(
    workspace_dir: Path,
    seeds: set[Path],
    excluded: set[Path],
) -> set[Path]:
    """Grow a member set by following its ``path`` dependencies.

    Cargo makes a path dependency that resides inside the workspace directory
    a member of that workspace, so the walk follows those edges transitively.
    A dependency outside the workspace, under ``exclude``, or without a
    manifest of its own is not a member and is not followed.

    Args:
        workspace_dir: Directory owning the workspace manifest.
        seeds: Members named by the manifest itself.
        excluded: Directories the workspace excludes.

    Returns:
        The seeds plus every package reachable from them. The visited set
        bounds the walk, so a dependency cycle terminates.
    """
    members = set(seeds)
    pending = list(seeds)
    while pending:
        current = pending.pop()
        data = _read_manifest(current / CARGO_MANIFEST)
        if data is None:
            continue
        for dependency in _path_dependencies(current, data):
            if dependency in members or not _is_within(dependency, workspace_dir):
                continue
            if any(_is_within(dependency, directory) for directory in excluded):
                continue
            if not (dependency / CARGO_MANIFEST).is_file():
                continue
            members.add(dependency)
            pending.append(dependency)
    return members


def _workspace_owns(manifest_dir: Path, data: dict[str, Any], roots: set[Path]) -> bool:
    """Report whether a workspace manifest owns every package root.

    Args:
        manifest_dir: Directory owning the manifest.
        data: The parsed manifest.
        roots: Package directories the Cargo command has to cover.

    Returns:
        ``True`` when ``data`` declares a ``[workspace]`` table whose members
        include every root — directly, through a glob, as the manifest's own
        directory, or as a ``path`` dependency inside the workspace — and no
        root falls under ``workspace.exclude``. The manifest's own directory
        always counts: a command run from a workspace root covers it whether
        or not the manifest also declares ``[package]``, and the root
        ``Cargo.toml`` is itself an input the Rust tools discover.
    """
    workspace = data.get("workspace")
    if not isinstance(workspace, dict):
        return False
    members = _resolve_patterns(manifest_dir, workspace.get("members"))
    members.add(manifest_dir)
    excluded = _resolve_patterns(manifest_dir, workspace.get("exclude"))
    members = _with_path_dependencies(manifest_dir, members, excluded)
    return all(
        root in members
        and not any(_is_within(root, directory) for directory in excluded)
        for root in roots
    )


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


def _nearest_workspace_root(
    start: Path,
    boundary: Path | None,
    roots: set[Path],
) -> tuple[Path | None, CargoRootIssue | None]:
    """Walk upward from ``start`` to the workspace that owns every root.

    A workspace that does not list all of the roots — because they sit in an
    excluded subtree, or belong to a workspace of their own — does not end
    the walk: an outer workspace may still own them.

    Args:
        start: Directory to begin the upward walk at, inclusive.
        boundary: Outermost repository the walk may reach, inclusive. Only
            this directory ends the walk, so a nested repository between
            ``start`` and ``boundary`` is walked through. ``None`` leaves the
            walk unbounded, for inputs that live outside any repository.
        roots: Package directories the workspace has to own.

    Returns:
        A ``(root, issue)`` pair. ``root`` is the directory owning the
        nearest ``Cargo.toml`` whose ``[workspace]`` table covers every root,
        with ``issue`` ``None``. When the walk reaches the boundary or the
        filesystem root without finding one, ``root`` is ``None`` and
        ``issue`` names why: a ``[package]``-only ancestor was passed, the
        repository boundary was hit, or nothing above the roots owns them.
    """
    package_only = False
    for candidate in [start, *start.parents]:
        manifest = candidate / CARGO_MANIFEST
        if manifest.is_file():
            data = _read_manifest(manifest)
            if data is not None:
                if _workspace_owns(candidate, data, roots):
                    return candidate, None
                if "package" in data and "workspace" not in data:
                    package_only = True
        if candidate == boundary:
            if package_only:
                return None, CargoRootIssue.PACKAGE_ONLY_ANCESTOR
            return None, CargoRootIssue.REPOSITORY_BOUNDARY
    if package_only:
        return None, CargoRootIssue.PACKAGE_ONLY_ANCESTOR
    return None, CargoRootIssue.NO_OWNING_WORKSPACE


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


def resolve_cargo_root(
    paths: list[str],
    *,
    tool_label: str | None = None,
) -> CargoRoot:
    """Return the directory a Cargo command should run from, with the reason.

    Each path is walked upward to the nearest ``Cargo.toml``. When the paths
    resolve to a single package that package's directory is returned. When they
    straddle several packages the walk continues upward from their common
    ancestor until a ``[workspace]`` manifest whose members cover every one of
    those packages is found, so a nested member set resolves to the workspace
    root rather than to one of its members. A workspace manifest always covers
    its own directory, so the root ``Cargo.toml`` the Rust tools discover does
    not make the workspace look unowned. An ancestor manifest that declares
    only ``[package]`` is rejected — running Cargo there would act on that
    crate alone — and so is a workspace that excludes the packages or never
    lists them. The walk stops at the outermost repository containing
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
        The Cargo root to use, or the reason no usable root exists.
    """
    roots = _nearest_manifest_dirs(paths)
    if not roots:
        return CargoRoot(root=None, issue=CargoRootIssue.NO_MANIFEST)

    unique_roots = set(roots)
    if len(unique_roots) == 1:
        return CargoRoot(root=roots[0], issue=None)

    try:
        common = Path(os.path.commonpath([str(root) for root in unique_roots]))
    except ValueError:
        if tool_label is not None:
            logger.warning(
                "Multiple Cargo roots found on different drives; cannot determine "
                "common workspace root. Skipping {}.",
                tool_label,
            )
        return CargoRoot(root=None, issue=CargoRootIssue.SEPARATE_DRIVES)

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
        return CargoRoot(root=None, issue=CargoRootIssue.SPLIT_REPOSITORIES)

    workspace_root, issue = _nearest_workspace_root(common, boundary, unique_roots)
    if workspace_root is not None:
        return CargoRoot(root=workspace_root, issue=None)

    if tool_label is not None:
        logger.warning(
            "Multiple Cargo roots found ({}) without a common workspace "
            "Cargo.toml. Consider creating a workspace or running {} on each "
            "crate separately.",
            ", ".join(str(root) for root in unique_roots),
            tool_label,
        )
    return CargoRoot(root=None, issue=issue)


def find_cargo_root(
    paths: list[str],
    *,
    tool_label: str | None = None,
) -> Path | None:
    """Return the directory a Cargo command should run from.

    Thin wrapper over :func:`resolve_cargo_root` for callers that do not need
    to explain a rejection.

    Args:
        paths: File or directory paths to search upward from.
        tool_label: Tool name used to explain an unresolvable multi-root
            layout to the user. When ``None`` the failure is silent.

    Returns:
        The Cargo root to use, or ``None`` when no usable root exists.
    """
    return resolve_cargo_root(paths, tool_label=tool_label).root


def cargo_package_args(paths: list[str], cargo_root: Path) -> list[str]:
    """Return the package-selection arguments for a Cargo command.

    ``cargo clippy`` run at a workspace root without a selection lints
    ``workspace.default-members`` when that key is set, so a touched crate
    outside the default set would report clean. Naming the input packages
    keeps the command honest.

    Args:
        paths: The file or directory paths the tool was handed.
        cargo_root: The directory the command will run from.

    Returns:
        ``["-p", name, ...]`` for the packages the paths belong to, or
        ``["--workspace"]`` when a name cannot be read. Empty when the root
        is a single package, which needs no selection.
    """
    data = _read_manifest(cargo_root / CARGO_MANIFEST)
    if data is None or not isinstance(data.get("workspace"), dict):
        return []
    names: list[str] = []
    for root in dict.fromkeys(_nearest_manifest_dirs(paths)):
        manifest = _read_manifest(root / CARGO_MANIFEST) or {}
        package = manifest.get("package")
        name = package.get("name") if isinstance(package, dict) else None
        if not isinstance(name, str) or not name:
            return ["--workspace"]
        if name not in names:
            names.append(name)
    if not names:
        return ["--workspace"]
    return [argument for name in names for argument in ("-p", name)]

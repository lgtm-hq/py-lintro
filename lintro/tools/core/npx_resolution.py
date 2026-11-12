"""Predict, offline, which executable ``npx --package <pkg>@<pin> <bin>`` runs.

The version gate reads a registry runner's version from its pin (#2043). For
npx that is only valid when npx really launches the pinned package. npm 11's
``exec`` decides as follows (``libnpmexec`` ``missingFromTree``,
``@npmcli/config`` ``loadLocalPrefix``, ``@npmcli/run-script``
``set-path.js``), verified offline against npx 11.20.0:

1. npm's project root is the nearest ancestor of the working directory with a
   ``package.json`` file or a ``node_modules`` directory, promoted to an
   ancestor workspace root when that root's ``workspaces`` include it.
2. Arborist loads the installed tree from that root: the root package, its
   workspaces (linked or not), every ``node_modules`` entry at any depth
   except dot entries, following symlinks once per real path, including link
   targets outside the root.
3. When any loaded package has ``name == <pkg>`` and a version semver-equal to
   ``<pin>``, npx skips the install and runs ``<bin>`` by name on a ``PATH``
   of ``node_modules/.bin`` in every ancestor of the working directory, then
   the inherited ``PATH``. With no such ``<bin>`` it exits 127.
4. Otherwise npx installs the pin into its cache, puts that cache's ``.bin``
   first on ``PATH`` and runs the pinned package.

The walk here deliberately over-approximates step 2. Finding a match npx would
not find can only make lintro probe a local binary, or refuse the tool. It can
never let an unprobed binary run behind the pin. Missing a match npx would find
could do exactly that, so anything the walk cannot settle is refused.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from lintro.enums.npx_launch import NpxLaunch
from lintro.tools.core.node_fallback import EXACT_SEMVER

#: Ceiling on installed package directories read for one decision. A tree
#: larger than this cannot be settled offline in reasonable time, so the
#: decision is UNDECIDED and the gate fails closed.
MAX_TREE_PACKAGES: int = 100_000


@dataclass(frozen=True)
class NpxDecision:
    """Offline prediction of what npx would run, with its evidence.

    Attributes:
        launch: Which executable npx would start.
        project_root: npm's project root for the working directory.
        binary: The ``node_modules/.bin`` entry npx would run, for LOCAL.
        reason: Why the tool cannot run, for NO_BIN and UNDECIDED.
    """

    launch: NpxLaunch
    project_root: Path
    binary: str | None = field(default=None)
    reason: str | None = field(default=None)


class _TreeTooLargeError(Exception):
    """The installed tree exceeded :data:`MAX_TREE_PACKAGES`."""


def _read_manifest(directory: Path) -> dict[str, object]:
    """Read ``package.json`` the way an Arborist error node degrades.

    Args:
        directory: Package directory.

    Returns:
        The parsed object, or an empty dict when it is missing or invalid.
    """
    try:
        data = json.loads((directory / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _workspace_dirs(root: Path, manifest: dict[str, object]) -> list[Path]:
    """Expand a root's ``workspaces`` globs to package directories.

    Negated patterns are ignored, which can only add directories: an extra
    directory widens the walk and never hides a match.

    Args:
        root: Directory holding *manifest*.
        manifest: Parsed root ``package.json``.

    Returns:
        Workspace directories that contain a ``package.json``.
    """
    declared = manifest.get("workspaces")
    if isinstance(declared, dict):
        declared = declared.get("packages")
    if not isinstance(declared, list):
        return []
    found: list[Path] = []
    for pattern in declared:
        if not isinstance(pattern, str) or not pattern or pattern.startswith("!"):
            continue
        try:
            matches = list(root.glob(pattern.rstrip("/")))
        except (OSError, ValueError, NotImplementedError):
            continue
        found.extend(match for match in matches if (match / "package.json").is_file())
    return found


def npm_project_root(cwd: Path) -> Path:
    """Locate npm's project root (``localPrefix``) for a working directory.

    Mirrors ``@npmcli/config`` ``loadLocalPrefix``: the nearest ancestor with a
    ``package.json`` file or a ``node_modules`` directory, then the first
    further ancestor whose ``workspaces`` include that directory.

    Args:
        cwd: Directory npx would run in.

    Returns:
        The project root, or *cwd* when no ancestor qualifies.
    """
    origin = cwd.resolve()
    prefix: Path | None = None
    for directory in (origin, *origin.parents):
        has_manifest = (directory / "package.json").is_file()
        if prefix is None:
            if has_manifest or (directory / "node_modules").is_dir():
                prefix = directory
            continue
        if not has_manifest:
            continue
        workspaces = _workspace_dirs(directory, _read_manifest(directory))
        if any(workspace.resolve() == prefix for workspace in workspaces):
            return directory
    return prefix if prefix is not None else origin


def _node_modules_entries(package_dir: Path) -> Iterator[Path]:
    """Yield the package directories under ``<package_dir>/node_modules``.

    Dot entries are skipped, as Arborist skips them. ``@scope`` directories are
    expanded one level.

    Args:
        package_dir: Real path of a loaded package.

    Yields:
        Path: Entry paths as they appear under ``node_modules``, unresolved.
    """
    modules = package_dir / "node_modules"
    try:
        entries = sorted(os.scandir(modules), key=lambda entry: entry.name)
    except OSError:
        return
    for entry in entries:
        if entry.name.startswith("."):
            continue
        path = modules / entry.name
        if not entry.name.startswith("@"):
            yield path
            continue
        try:
            scoped = sorted(os.scandir(path), key=lambda kid: kid.name)
        except OSError:
            continue
        for kid in scoped:
            if not kid.name.startswith("."):
                yield path / kid.name


def _normalized_version(version: object) -> str | None:
    """Reduce a version to what ``semver.satisfies`` compares for an exact pin.

    A leading ``v`` and surrounding whitespace are accepted, and build
    metadata is ignored, as semver does.

    Args:
        version: Raw ``version`` value from ``package.json``.

    Returns:
        ``MAJOR.MINOR.PATCH[-PRERELEASE]``, or None when it is not a version.
    """
    if not isinstance(version, str):
        return None
    candidate = version.strip()
    if candidate.startswith("v"):
        candidate = candidate[1:]
    if not EXACT_SEMVER.fullmatch(candidate):
        return None
    return candidate.split("+", 1)[0]


def _matches(directory: Path, package: str, pin: str) -> bool:
    """Report whether a package directory is ``<package>`` at the pin.

    Args:
        directory: Package directory (symlinks are read through).
        package: npm package name.
        pin: Normalised pinned version.

    Returns:
        True when ``package.json`` names *package* at a version equal to *pin*.
    """
    manifest = _read_manifest(directory)
    return (
        manifest.get("name") == package
        and _normalized_version(manifest.get("version")) == pin
    )


def _ancestor_candidates(real: Path, root: Path, package: str) -> Iterator[Path]:
    """Yield ``<ancestor>/node_modules/<package>`` dirs Arborist may also load.

    Arborist resolves a loaded package's missing dependencies from
    ``node_modules`` in its ancestors, up to the common ancestor with the
    project root (``#findMissingEdges``). Only directories named *package*
    can match, so only those are checked.

    Args:
        real: Real path of a loaded package.
        root: Real path of the project root.
        package: npm package name.

    Yields:
        Path: Candidate package directories.
    """
    try:
        common = Path(os.path.commonpath([real, root]))
    except ValueError:
        return
    for ancestor in real.parents:
        if len(ancestor.parts) < len(common.parts):
            return
        yield ancestor / "node_modules" / package
        if ancestor == common:
            return


def tree_has_exact_version(
    root: Path,
    package: str,
    version: str,
    *,
    max_packages: int = MAX_TREE_PACKAGES,
) -> bool | None:
    """Report whether npm's installed tree holds *package* at *version*.

    Args:
        root: npm project root, from :func:`npm_project_root`.
        package: npm package name.
        version: Exact pinned version.
        max_packages: Most package directories to read.

    Returns:
        True or False, or None when the tree exceeded *max_packages*.
    """
    pin = _normalized_version(version)
    if pin is None:
        return None
    real_root = root.resolve()
    try:
        return _walk_for_match(real_root, package, pin, max_packages)
    except _TreeTooLargeError:
        return None


def _walk_for_match(root: Path, package: str, pin: str, max_packages: int) -> bool:
    """Walk the installed tree depth-first, stopping at the first match.

    Args:
        root: Real path of the project root.
        package: npm package name.
        pin: Normalised pinned version.
        max_packages: Most package directories to read.

    Returns:
        Whether a matching package was found.

    Raises:
        _TreeTooLargeError: More than *max_packages* directories were read.
    """
    stack: list[Path] = [root, *_workspace_dirs(root, _read_manifest(root))]
    seen: set[Path] = set()
    checked: set[Path] = set()
    while stack:
        directory = stack.pop()
        try:
            real = directory.resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if real in seen or not real.is_dir():
            continue
        seen.add(real)
        if len(seen) > max_packages:
            raise _TreeTooLargeError
        if _matches(directory, package, pin):
            return True
        for candidate in _ancestor_candidates(real, root, package):
            if candidate not in checked:
                checked.add(candidate)
                if _matches(candidate, package, pin):
                    return True
        stack.extend(_node_modules_entries(real))
    return False


def npx_path_binary(binary: str, cwd: Path) -> str | None:
    """Find the ``<bin>`` npx runs by name when it skips the install.

    Mirrors ``set-path.js``: ``node_modules/.bin`` in the working directory
    and every ancestor up to the filesystem root, nearest first.

    Args:
        binary: Executable name.
        cwd: Directory npx would run in.

    Returns:
        POSIX path of the first executable entry, or None.
    """
    origin = cwd.resolve()
    name = f"{binary}.cmd" if sys.platform == "win32" else binary
    for directory in (origin, *origin.parents):
        candidate = directory / "node_modules" / ".bin" / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.as_posix()
    return None


def decide_npx_launch(
    *,
    package: str,
    version: str,
    binary: str,
    cwd: Path,
    max_packages: int = MAX_TREE_PACKAGES,
) -> NpxDecision:
    """Predict what ``npx --package <package>@<version> <binary>`` would run.

    Args:
        package: npm package name.
        version: Exact pinned version.
        binary: Executable name passed to npx.
        cwd: Directory npx would run in.
        max_packages: Most package directories to read.

    Returns:
        The predicted launch and its evidence.
    """
    root = npm_project_root(cwd)
    present = tree_has_exact_version(
        root,
        package,
        version,
        max_packages=max_packages,
    )
    if present is None:
        return NpxDecision(
            launch=NpxLaunch.UNDECIDED,
            project_root=root,
            reason=(
                f"cannot tell offline whether npx would run {package}@{version}: "
                f"the installed tree under {root} has more than {max_packages} "
                "packages"
            ),
        )
    if not present:
        return NpxDecision(launch=NpxLaunch.PINNED, project_root=root)
    local = npx_path_binary(binary, cwd)
    if local is None:
        return NpxDecision(
            launch=NpxLaunch.NO_BIN,
            project_root=root,
            reason=(
                f"npx would find {package}@{version} installed under {root} "
                f"but no {binary} on its PATH, so it would not run"
            ),
        )
    return NpxDecision(launch=NpxLaunch.LOCAL, project_root=root, binary=local)

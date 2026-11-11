"""Project anchoring shared by tool execution and capability probes."""

import os
from pathlib import Path

from lintro.utils.path_utils import find_file_upward


def get_cwd(paths: list[str]) -> str | None:
    """Get common parent directory for paths.

    Args:
        paths: Paths to compute common parent for.

    Returns:
        Common parent directory path, or None if not applicable.
    """
    if not paths:
        return None

    # Get the parent directory for each path
    # For files: use dirname; for directories: use the path itself
    parent_dirs: set[str] = set()
    for p in paths:
        abs_path = os.path.abspath(p)
        if os.path.isdir(abs_path):
            parent_dirs.add(abs_path)
        else:
            parent_dirs.add(os.path.dirname(abs_path))

    if len(parent_dirs) == 1:
        return parent_dirs.pop()

    try:
        return os.path.commonpath(list(parent_dirs))
    except ValueError:
        # Can happen on Windows with paths on different drives
        return None


#: Markers that identify a project root, probed in order in each directory
#: while walking up from the discovered files. ``.git`` covers repositories
#: (a directory normally, a file in worktrees/submodules, so existence — not
#: is-dir — is what matters); the rest cover language projects. The *nearest*
#: marker wins, so a file in a nested project (e.g. a monorepo package with its
#: own ``package.json``/``tsconfig.json``) anchors to that project — the
#: directory a tool's own config discovery expects — rather than the outer repo.
_PROJECT_ROOT_MARKERS: tuple[str, ...] = (
    ".git",
    "pyproject.toml",
    "package.json",
    "setup.py",
    "setup.cfg",
    "go.mod",
    "Cargo.toml",
)

#: Bounds the upward marker search, counting the files' common ancestor itself,
#: so an unrelated marker from a far filesystem ancestor (e.g. a dotfile-managed
#: ``~/.git``, or a distant vendored ``package.json``) is never picked up.
#: Matches ``_IGNORE_SEARCH_MAX_DEPTH`` used for ``.lintro-ignore`` discovery.
_PROJECT_ROOT_SEARCH_MAX_DEPTH = 20


def get_execution_cwd(
    files: list[str],
    *,
    max_depth: int = _PROJECT_ROOT_SEARCH_MAX_DEPTH,
) -> str:
    """Return a stable, scope-independent working directory for tool subprocesses.

    The tool subprocess is anchored to the **project root** of the discovered
    files — the nearest ancestor directory holding a project marker (``.git``,
    ``pyproject.toml``, ``package.json``, ...), found by walking up from the
    files' common ancestor.

    Unlike :func:`get_cwd` (the raw common ancestor), this does not move with
    the input scope: within one project, walking up from a single file's
    directory or from the whole-repo common ancestor reaches the *same* marker,
    so the path a tool sees for a given file — and thus any config ``overrides``
    keyed on that path — is identical whether the user passed a file, a
    directory, or ``.`` (#1616). Because the anchor is derived from the files
    (not the process cwd) and uses the *nearest* marker, each tool's own config
    discovery still resolves against the project the files actually belong to,
    including nested projects in a monorepo.

    Falls back to the files' common ancestor when no marker is found within
    ``_PROJECT_ROOT_SEARCH_MAX_DEPTH`` ancestors (preserving prior behavior for
    marker-less trees, and avoiding a distant unrelated marker), and to the
    process cwd when there are no files.

    Note:
        When a single invocation spans multiple nested projects, the common
        ancestor (and thus the anchor) is the outer project; per-project
        anchoring in that case would require grouping files by project root and
        running the tool once per group.

    Args:
        files: Discovered file paths the tool will process.
        max_depth: Maximum number of ancestors to search.

    Returns:
        Absolute path to use as the tool subprocess working directory.
    """
    common = get_cwd(files)
    if common is None:
        return os.getcwd()
    marker = find_file_upward(
        Path(common),
        _PROJECT_ROOT_MARKERS,
        max_depth=max_depth,
    )
    if marker is not None:
        return str(marker.parent)
    return common

"""Find targets a directory-scoped pack cannot match from Semgrep's cwd (#3437).

Semgrep matches a rule's ``paths.include`` globs against target paths relative
to its working directory. Lintro runs it from the project root (the nearest
directory holding a project marker), where ``**/scripts/**`` sees
``scripts/fmt.sh``. Without a marker that directory falls back to the files'
common ancestor, so scanning ``scripts/`` hands Semgrep ``fmt.sh`` (or, for
``scripts/sub/fmt.sh``, runs from ``scripts/sub``) and the glob matches
nothing. The working directory is not moved, because user rules and a
relative ``config`` resolve against it; the run is reported incomplete instead.
"""

from __future__ import annotations

import os
from pathlib import PurePath

from lintro.rules import rule_pack_info
from lintro.utils.execution_paths import has_project_marker, project_root_markers


def _scope_root(scope: str) -> str:
    """Return the directory a requested scan path covers.

    Args:
        scope: Path the user asked lintro to scan.

    Returns:
        The absolute directory itself, or a file's parent directory.
    """
    absolute = os.path.abspath(scope)
    return absolute if os.path.isdir(absolute) else os.path.dirname(absolute)


def _is_within(path: str, ancestor: str) -> bool:
    """Report whether ``path`` is ``ancestor`` or lies below it.

    Args:
        path: Absolute, normalized path.
        ancestor: Absolute, normalized directory.

    Returns:
        Whether ``ancestor`` contains ``path``.
    """
    try:
        return os.path.commonpath([path, ancestor]) == ancestor
    except ValueError:
        return False


def _scoped_directories(target: str, cwd: str, scopes: list[str]) -> set[str]:
    """Name the in-scope directories at or above Semgrep's working directory.

    The scope starts at the directory lintro was started in when that contains
    the scanned path, so it covers the invocation directory itself and every
    directory a relative scan path names. A path outside the invocation
    directory starts the scope at its own root. Directories between that start
    and ``cwd`` count; ancestors above the start do not, so an unrelated
    ``scripts`` directory high above a checkout never counts.

    Args:
        target: Absolute target path.
        cwd: Absolute directory Semgrep runs from.
        scopes: Paths the user asked lintro to scan, as typed.

    Returns:
        Directory names in the scan scope that Semgrep's relative paths omit.
    """
    roots = [root for scope in scopes if _is_within(target, root := _scope_root(scope))]
    if not roots:
        return {PurePath(cwd).name}
    # The widest covering scope: every directory the user asked to scan counts.
    root = min(roots, key=len)
    invoked = os.path.abspath(os.getcwd())
    start = invoked if _is_within(root, invoked) else root
    # When cwd lies above ``start``, this adds only "..": the target's relative
    # path then passes through ``start``, so a pack directory there is visible.
    return {PurePath(start).name, *PurePath(os.path.relpath(cwd, start)).parts}


def hidden_pack_targets(
    *,
    packs: list[str],
    cwd: str,
    files: list[str],
    scopes: list[str],
) -> str | None:
    """Explain targets that enabled directory-scoped packs cannot match.

    With a project marker in ``cwd``, or with no directory-scoped pack enabled,
    nothing is hidden. Otherwise a target is hidden when a directory a pack's
    glob names (``scripts`` for ``**/scripts/**``) lies in the scan scope at or
    above ``cwd``, so Semgrep sees the target without it, and no such directory
    appears in the relative path Semgrep does see.

    Args:
        packs: Validated, enabled pack names.
        cwd: Directory Semgrep runs from.
        files: Targets relative to ``cwd``.
        scopes: Paths the user asked lintro to scan, as typed.

    Returns:
        The reason the run is incomplete, or None when nothing is hidden.
    """
    affected = [name for name in packs if rule_pack_info(name).anchored_directories]
    if not affected or has_project_marker(cwd):
        return None
    directories = list(
        dict.fromkeys(
            directory
            for name in affected
            for directory in rule_pack_info(name).anchored_directories
        ),
    )
    # A deeper glob such as ``tools/scripts/`` is reduced to its last name.
    anchors = frozenset(PurePath(directory).name for directory in directories)
    base = os.path.abspath(cwd)
    hidden = [
        path
        for path in files
        if not anchors.intersection(PurePath(path).parent.parts)
        and anchors.intersection(
            _scoped_directories(os.path.join(base, path), base, scopes),
        )
    ]
    if not hidden:
        return None
    return (
        f"[semgrep] {', '.join(affected)}: {len(hidden)} file(s) under "
        f"{', '.join(directories)} were not checked by these packs (first: "
        f"{hidden[0]}) because no project marker was found in {cwd}, so Semgrep "
        f"ran from inside that directory. Add one of "
        f"{', '.join(project_root_markers())} at the project root."
    )

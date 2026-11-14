"""Choose where Semgrep runs so directory-scoped packs can match (#3437).

Semgrep matches a rule's ``paths.include`` globs against target paths relative
to its working directory. Lintro normally runs it from the project root (the
nearest directory holding a project marker), where ``**/scripts/**`` sees
``scripts/fmt.sh``. Without a marker that directory falls back to the files'
common ancestor, so scanning ``scripts/`` alone hands Semgrep ``fmt.sh`` and the
glob matches nothing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import PurePath

from lintro.rules import rule_pack_info
from lintro.utils.execution_paths import has_project_marker, project_root_markers


@dataclass(frozen=True)
class ScanRoot:
    """Working directory and targets for one Semgrep subprocess.

    Attributes:
        cwd: Directory Semgrep runs from.
        files: Targets relative to ``cwd``.
        hidden: Targets, relative to ``cwd``, that an enabled directory-scoped
            pack cannot match from ``cwd`` because the directory its glob names
            lies at or above ``cwd``.
        warning: Explanation for ``hidden``, or None when nothing is hidden.
    """

    cwd: str
    files: list[str]
    hidden: list[str]
    warning: str | None


def _contains_run(parts: tuple[str, ...], run: tuple[str, ...], below: int) -> bool:
    """Report whether ``run`` occurs consecutively in ``parts`` before ``below``.

    Args:
        parts: Path components to search.
        run: Consecutive components to find.
        below: Exclusive upper bound for the index where ``run`` starts.

    Returns:
        Whether ``run`` starts at some index lower than ``below``.
    """
    last = min(below, len(parts) - len(run) + 1)
    return any(parts[i : i + len(run)] == run for i in range(max(last, 0)))


def _is_hidden(
    absolute: PurePath,
    root: PurePath,
    anchors: list[tuple[str, ...]],
) -> bool:
    """Report whether a pack directory holds a file but cannot match it from root.

    Args:
        absolute: Absolute target path.
        root: Directory Semgrep runs from; an ancestor of ``absolute``.
        anchors: Directory component runs named by pack globs.

    Returns:
        True when an anchor names a directory of ``absolute`` that starts at or
        above ``root`` and no anchor matches inside the root-relative path.
    """
    directories = absolute.parent.parts
    relative = absolute.relative_to(root).parent.parts
    above_root = len(root.parts)
    return any(
        _contains_run(directories, anchor, above_root) for anchor in anchors
    ) and not any(_contains_run(relative, anchor, len(relative)) for anchor in anchors)


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


def choose_scan_root(
    *,
    packs: list[str],
    cwd: str,
    files: list[str],
    run_cwd: str,
) -> ScanRoot:
    """Anchor Semgrep where enabled directory-scoped packs can match.

    With a project marker in ``cwd``, or with no directory-scoped pack enabled,
    the scan is unchanged. Otherwise Semgrep runs from ``run_cwd`` (the
    directory Lintro was started in) when it contains ``cwd``, so scanning
    ``scripts`` from a marker-less tree still hands Semgrep ``scripts/fmt.sh``.
    Any target the packs still cannot match from that directory is reported in
    ``hidden``, so the caller can mark the result incomplete instead of passing.

    Args:
        packs: Validated, enabled pack names.
        cwd: Execution directory Lintro chose for the scan.
        files: Targets relative to ``cwd``.
        run_cwd: Directory Lintro was started in.

    Returns:
        The directory, the targets relative to it, and any hidden targets.
    """
    affected = [name for name in packs if rule_pack_info(name).anchored_directories]
    if not affected or has_project_marker(cwd):
        return ScanRoot(cwd=cwd, files=files, hidden=[], warning=None)
    directories = list(
        dict.fromkeys(
            directory
            for name in affected
            for directory in rule_pack_info(name).anchored_directories
        ),
    )
    anchors = [PurePath(directory).parts for directory in directories]
    base = os.path.realpath(cwd)
    started = os.path.realpath(run_cwd)
    root = started if _is_within(base, started) else base
    absolutes = [PurePath(os.path.normpath(os.path.join(base, f))) for f in files]
    relocated = [os.path.relpath(path, root) for path in absolutes]
    hidden = [
        relative
        for path, relative in zip(absolutes, relocated, strict=True)
        if _is_hidden(path, PurePath(root), anchors)
    ]
    warning = None
    if hidden:
        warning = (
            f"[semgrep] {', '.join(affected)}: {len(hidden)} file(s) under "
            f"{', '.join(directories)} were not checked by these packs (first: "
            f"{hidden[0]}) because no project marker was found in {root}. Add one "
            f"of {', '.join(project_root_markers())} at the project root, or run "
            "lintro from the directory that contains them."
        )
    return ScanRoot(cwd=root, files=relocated, hidden=hidden, warning=warning)

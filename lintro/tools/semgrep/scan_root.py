"""Find targets a directory-scoped pack cannot match from Semgrep's cwd (#3437).

Semgrep matches a rule's ``paths.include`` globs against target paths relative
to its working directory. Lintro runs it from the project root (the nearest
directory holding a project marker), where ``**/scripts/**`` sees
``scripts/fmt.sh``. Without a marker that directory falls back to the files'
common ancestor, so scanning ``scripts/`` alone hands Semgrep ``fmt.sh`` and the
glob matches nothing. The working directory is not moved, because user rules
and a relative ``config`` resolve against it; the run is reported incomplete
instead.
"""

from __future__ import annotations

from pathlib import PurePath

from lintro.rules import rule_pack_info
from lintro.utils.execution_paths import has_project_marker, project_root_markers


def _is_hidden(target: str, cwd_name: str, anchors: frozenset[str]) -> bool:
    """Report whether a pack directory holds a target Semgrep cannot match.

    Only the working directory itself and directories below it count: an
    ancestor that happens to share a pack directory's name lies outside the
    scanned tree, so the pack was never meant to cover it.

    Args:
        target: Target path relative to the working directory.
        cwd_name: Last component of the working directory.
        anchors: Directory names that pack globs require.

    Returns:
        True when the working directory is a pack directory, so the pack would
        cover the target from a project root, and no pack directory appears in
        the relative path Semgrep sees.
    """
    relative = PurePath(target).parent.parts
    if anchors.intersection(relative):
        return False
    return cwd_name in anchors


def hidden_pack_targets(
    *,
    packs: list[str],
    cwd: str,
    files: list[str],
) -> str | None:
    """Explain targets that enabled directory-scoped packs cannot match.

    With a project marker in ``cwd``, or with no directory-scoped pack enabled,
    nothing is hidden. Otherwise a target is hidden when the working directory
    is itself a directory a pack's glob names (``scripts`` for
    ``**/scripts/**``), because Semgrep sees the target without that prefix.

    Args:
        packs: Validated, enabled pack names.
        cwd: Directory Semgrep runs from.
        files: Targets relative to ``cwd``.

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
    # A deeper glob such as ``tools/scripts/`` is reduced to its last name,
    # the one the working directory would have to carry.
    anchors = frozenset(PurePath(directory).name for directory in directories)
    cwd_name = PurePath(cwd).name
    hidden = [path for path in files if _is_hidden(path, cwd_name, anchors)]
    if not hidden:
        return None
    return (
        f"[semgrep] {', '.join(affected)}: {len(hidden)} file(s) under "
        f"{', '.join(directories)} were not checked by these packs (first: "
        f"{hidden[0]}) because no project marker was found in {cwd}, so Semgrep "
        f"ran from inside that directory. Add one of "
        f"{', '.join(project_root_markers())} at the project root."
    )

"""Snapshot-and-diff support for the MCP ``lintro_format`` tool.

``lintro fmt --dry-run`` already exists, but it answers a different question:
it runs the fix-capable tools in *check* mode and reports which issues a real
run would address. It produces no diff, because no tool ever wrote anything.
An agent asking "what would you change?" needs the actual bytes.

Getting real diffs means letting the formatters actually write. Three
mechanisms were considered:

* **Temp copy of the tree.** Rejected: formatters resolve their configuration
  (``.prettierrc``, ``pyproject.toml``, ``node_modules``) by walking up from
  each file, so a partial copy formats under the wrong config and a full copy
  is unbounded.
* **Git checkpoint + ``git restore``** (``lintro.ai.checkpoints``). Rejected as
  the primary path: it only works inside a git work tree, and it cannot
  represent changes to files git does not track.
* **In-place with a byte snapshot** — used here. The files the selected tools
  would touch are read into memory first, the real ``fmt`` runs, diffs are
  computed from the snapshot against what is now on disk, and for a dry run
  every changed file is written back from the snapshot in a ``finally``.

The snapshot set comes from the tools' own file discovery, so it is exactly
the set they can write to, and a total-size ceiling turns a pathological
workspace into a structured error *before* anything runs rather than an
out-of-memory kill halfway through.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from lintro.mcp.enums.mcp_error_code import McpErrorCode
from lintro.mcp.errors import McpError

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "MAX_SNAPSHOT_BYTES",
    "FileChange",
    "changes_since",
    "restore",
    "snapshot_files",
]

# Ceiling on the total bytes held in memory for one dry run. Sized to be far
# above any plausible formattable source tree while still bounding the damage a
# workspace full of generated files can do to the server process.
MAX_SNAPSHOT_BYTES: Final[int] = 256 * 1024 * 1024

_BINARY_DIFF_NOTE: Final[str] = "Binary files differ; no textual diff available.\n"


@dataclass(frozen=True)
class FileChange:
    """A single file's before/after difference.

    Attributes:
        path: Workspace-relative POSIX path of the changed file, or its
            absolute path when it resolved outside the workspace.
        diff: Unified diff from the pre-run bytes to the post-run bytes, or a
            note when either side is not decodable as UTF-8 text.
    """

    path: str
    diff: str

    def to_dict(self) -> dict[str, Any]:
        """Serialize the change to a JSON-compatible dict.

        Returns:
            dict[str, Any]: Mapping with ``file`` and ``diff``.
        """
        return {"file": self.path, "diff": self.diff}


def snapshot_files(
    *,
    paths: list[str],
    tool_names: Sequence[str],
) -> dict[Path, bytes]:
    """Read the current bytes of every file the given tools could rewrite.

    Args:
        paths: Scan targets for this run.
        tool_names: Tools the run will execute.

    Returns:
        dict[Path, bytes]: Resolved file path to its current contents.

    Raises:
        McpError: :attr:`McpErrorCode.EXECUTION_ERROR` when the snapshot would
            exceed :data:`MAX_SNAPSHOT_BYTES`, or when a candidate file cannot
            be read (see below).
    """
    from lintro.plugins.file_discovery import discover_files, setup_exclude_patterns
    from lintro.tools import tool_manager

    exclude_patterns = setup_exclude_patterns([])
    candidates: set[Path] = set()
    for tool_name in tool_names:
        # Names normally arrive already resolved by ``get_tools_to_run``. The
        # guard is for a caller that passes raw names: an unresolvable one
        # contributes no files, which is the same outcome the run itself has.
        try:
            definition = tool_manager.get_tool(tool_name).definition
        except (KeyError, ValueError, AttributeError):
            continue
        for discovered in discover_files(
            paths,
            definition,
            exclude_patterns,
            include_venv=False,
            show_progress=False,
        ):
            candidates.add(Path(discovered).resolve())

    snapshot: dict[Path, bytes] = {}
    total = 0
    for candidate in sorted(candidates):
        if not candidate.is_file():
            continue
        # Deliberately *not* filtered to the workspace. A file discovery
        # reaches through a symlink is a file the formatters will write to, and
        # anything they can write to has to be restorable; dropping it here
        # would leave a dry run's edits on disk.
        try:
            data = candidate.read_bytes()
        except OSError as exc:
            # A file discovery reached is a file the formatters may write to,
            # and ``restore`` can only put back what the snapshot holds. Losing
            # one here would silently break the dry run's core promise, so the
            # run does not start at all.
            raise McpError(
                code=McpErrorCode.EXECUTION_ERROR,
                message=(
                    "Could not read a file the format run may modify; refusing "
                    "to start without a complete pre-run snapshot"
                ),
                detail={
                    "reason": "snapshot_read_failed",
                    "file": str(candidate),
                    "error": str(exc),
                },
            ) from exc
        total += len(data)
        if total > MAX_SNAPSHOT_BYTES:
            raise McpError(
                code=McpErrorCode.EXECUTION_ERROR,
                message=(
                    "Refusing to preview a format run over more than "
                    f"{MAX_SNAPSHOT_BYTES} bytes of source; narrow 'paths' or "
                    "'tools', or call again with dry_run=false"
                ),
                detail={
                    "reason": "snapshot_too_large",
                    "max_snapshot_bytes": MAX_SNAPSHOT_BYTES,
                },
            )
        snapshot[candidate] = data
    return snapshot


def _unified_diff(*, before: bytes, after: bytes, relative: str) -> str:
    """Render a unified diff between two byte blobs.

    Args:
        before: Pre-run contents.
        after: Post-run contents.
        relative: Workspace-relative path used in the diff headers.

    Returns:
        str: The unified diff, or a note when either side is not UTF-8 text.
    """
    try:
        before_text = before.decode("utf-8")
        after_text = after.decode("utf-8")
    except UnicodeDecodeError:
        return _BINARY_DIFF_NOTE
    return "".join(
        difflib.unified_diff(
            before_text.splitlines(keepends=True),
            after_text.splitlines(keepends=True),
            fromfile=f"a/{relative}",
            tofile=f"b/{relative}",
        ),
    )


def _display_path(*, path: Path, workspace: Path) -> str:
    """Render a changed file's path for the caller.

    Args:
        path: Resolved path of the changed file.
        workspace: Workspace root.

    Returns:
        str: The workspace-relative POSIX path, or the absolute path when the
        file resolved outside the workspace (reached through a symlink).
    """
    if path.is_relative_to(workspace):
        return path.relative_to(workspace).as_posix()
    return path.as_posix()


def changes_since(
    *,
    snapshot: dict[Path, bytes],
    workspace: Path,
) -> list[FileChange]:
    """Diff a snapshot against what is now on disk.

    Args:
        snapshot: Pre-run contents keyed by resolved path.
        workspace: Workspace root, for rendering relative paths.

    Returns:
        list[FileChange]: One entry per changed file, ordered by path. A file
        that was deleted during the run is reported as a diff to empty.

    Raises:
        McpError: :attr:`McpErrorCode.EXECUTION_ERROR` when a snapshotted file
            could not be read back, which would silently truncate the report.
    """
    changes: list[FileChange] = []
    unreadable: list[str] = []
    for path, before in sorted(snapshot.items()):
        try:
            after = path.read_bytes() if path.is_file() else b""
        except OSError as exc:
            unreadable.append(f"{path}: {exc}")
            continue
        if after == before:
            continue
        relative = _display_path(path=path, workspace=workspace)
        changes.append(
            FileChange(
                path=relative,
                diff=_unified_diff(before=before, after=after, relative=relative),
            ),
        )
    if unreadable:
        # A silently short diff would understate what the run did. Restoring
        # still happens: this raises inside the caller's ``try``, so a dry
        # run's ``finally`` puts the tree back before the error surfaces.
        raise McpError(
            code=McpErrorCode.EXECUTION_ERROR,
            message="Could not read every candidate file while computing the diff",
            detail={"reason": "diff_read_failed", "files": unreadable},
        )
    return changes


def restore(*, snapshot: dict[Path, bytes]) -> None:
    """Write the snapshot back over every file whose bytes changed.

    Only differing files are rewritten, so an untouched tree keeps its
    original mtimes and no downstream build cache is invalidated for nothing.
    A file the run deleted is recreated.

    Args:
        snapshot: Pre-run contents keyed by resolved path.

    Raises:
        McpError: :attr:`McpErrorCode.EXECUTION_ERROR` listing the files that
            could not be restored. Leaving a dry run's writes on disk silently
            would break the tool's core promise, so the failure is loud.
    """
    failures: list[str] = []
    for path, before in snapshot.items():
        try:
            if path.is_file() and path.read_bytes() == before:
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(before)
        except OSError as exc:  # pragma: no cover - filesystem failure
            failures.append(f"{path}: {exc}")
    if failures:
        raise McpError(
            code=McpErrorCode.EXECUTION_ERROR,
            message=(
                "Dry-run preview could not restore every file it formatted; "
                "the workspace has been modified"
            ),
            detail={"reason": "restore_failed", "files": failures},
        )

"""Map changed files to the tools relevant to them.

Watch mode only runs tools that actually apply to the files that changed,
rather than re-running the whole suite on every keystroke. The mapping is
derived from each tool's ``file_patterns`` (the same globs the normal file
discovery uses), so it stays in sync with the tool registry automatically
instead of hard-coding a static extension table.
"""

from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path

from lintro.tools import tool_manager

__all__ = ["get_tools_for_file", "select_tools_for_files"]


def _matches(filename: str, pattern: str) -> bool:
    """Return whether a filename matches a single glob pattern.

    Matching is performed against the basename, mirroring how the tool
    definitions express patterns (e.g. ``*.py``, ``Dockerfile.*``,
    ``test_*.py``).

    Args:
        filename: Basename of the file (no directory component).
        pattern: Glob pattern from a tool definition.

    Returns:
        True if the pattern matches the filename.
    """
    return fnmatch(filename, pattern)


def get_tools_for_file(
    path: str,
    *,
    available_tools: dict[str, object] | None = None,
) -> list[str]:
    """Return the sorted names of tools that apply to a single file.

    Args:
        path: Path to the changed file.
        available_tools: Optional pre-fetched mapping of tool name to plugin
            (as returned by ``tool_manager.get_all_tools()``). Injectable so
            tests need not touch the real registry; defaults to the live
            registry when omitted.

    Returns:
        Sorted list of tool names whose ``file_patterns`` match ``path``.
    """
    tools = available_tools if available_tools is not None else _all_tools()
    filename = Path(path).name

    selected: list[str] = []
    for name, plugin in tools.items():
        patterns = getattr(plugin.definition, "file_patterns", []) or []
        if any(_matches(filename, pattern) for pattern in patterns):
            selected.append(name)
    return sorted(selected)


def select_tools_for_files(
    paths: list[str],
    *,
    restrict_to: list[str] | None = None,
    available_tools: dict[str, object] | None = None,
) -> list[str]:
    """Return the union of tools relevant to a batch of changed files.

    Args:
        paths: Changed file paths.
        restrict_to: Optional user-supplied allowlist of tool names. When
            provided, the result is the intersection of the matched tools and
            this list (case-insensitive), preserving smart selection while
            honouring an explicit ``--tools`` filter.
        available_tools: Optional pre-fetched tool mapping (see
            :func:`get_tools_for_file`).

    Returns:
        Sorted list of unique tool names to run for this batch.
    """
    tools = available_tools if available_tools is not None else _all_tools()

    matched: set[str] = set()
    for path in paths:
        matched.update(get_tools_for_file(path, available_tools=tools))

    if restrict_to is not None:
        allowed = {name.lower() for name in restrict_to}
        matched = {name for name in matched if name.lower() in allowed}

    return sorted(matched)


def _all_tools() -> dict[str, object]:
    """Return the live tool registry mapping.

    Returns:
        Mapping of tool name to plugin instance.
    """
    return tool_manager.get_all_tools()

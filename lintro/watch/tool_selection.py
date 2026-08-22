"""Map changed files to the tools relevant to them.

Watch mode only runs tools that actually apply to the files that changed,
rather than re-running the whole suite on every keystroke. The mapping is
derived from each tool's ``file_patterns`` (the same globs the normal file
discovery uses), so it stays in sync with the tool registry automatically
instead of hard-coding a static extension table.

Smart selection must also stay compatible with
:func:`~lintro.utils.execution.tool_configuration.get_tools_to_run`: a raw
file-pattern union includes advisory finders, pytest, and catch-all ``*``
scanners that the check/fmt executor rejects or would re-run on every save.
"""

from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path

from lintro.enums.tool_name import ToolName
from lintro.plugins.base import BaseToolPlugin
from lintro.tools import tool_manager
from lintro.utils.execution.tool_configuration import _is_advisory_tool

__all__ = ["get_tools_for_file", "select_tools_for_files"]

_CATCH_ALL_PATTERNS: frozenset[str] = frozenset({"*"})


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


def _is_pytest_tool(name: str) -> bool:
    """Return whether ``name`` is the pytest tool (any spelling).

    Args:
        name: Tool name as registered or requested.

    Returns:
        True when the name is pytest.
    """
    return name.replace("-", "_").lower() == ToolName.PYTEST.value.lower()


def _has_catch_all_pattern(plugin: BaseToolPlugin) -> bool:
    """Return whether a tool's patterns include a universal ``*``.

    Args:
        plugin: Tool plugin whose definition is inspected.

    Returns:
        True when any file pattern is the catch-all ``*``.
    """
    patterns = getattr(plugin.definition, "file_patterns", []) or []
    return any(pattern in _CATCH_ALL_PATTERNS for pattern in patterns)


def _is_watch_compatible(
    name: str,
    plugin: BaseToolPlugin,
    *,
    auto_fix: bool,
    explicit: bool,
    honor_enabled: bool,
) -> bool:
    """Return whether a pattern-matched tool may be passed to the executor.

    Args:
        name: Registered tool name.
        plugin: Tool plugin for ``name``.
        auto_fix: When True, keep only tools that declare ``can_fix``.
        explicit: When True, the user named this tool in ``--tools`` /
            ``watch.tools``, so catch-all scanners stay eligible.
        honor_enabled: When True, drop tools disabled by config (default
            smart-selection). Explicit ``--tools`` bypasses this, matching
            ``get_tools_to_run``.

    Returns:
        True when the tool is safe to pass as explicit ``--tools``.
    """
    if _is_pytest_tool(name=name):
        return False
    if getattr(plugin.definition, "is_advisory", False) or _is_advisory_tool(
        name=name,
    ):
        return False
    if _has_catch_all_pattern(plugin=plugin) and not explicit:
        return False
    if auto_fix and not getattr(plugin.definition, "can_fix", False):
        return False
    if honor_enabled:
        from lintro.config.config_loader import get_config

        if not get_config().is_tool_enabled(name):
            return False
    return True


def get_tools_for_file(
    path: str,
    *,
    available_tools: dict[str, BaseToolPlugin] | None = None,
) -> list[str]:
    """Return the sorted names of tools that apply to a single file.

    This is the raw file-pattern match. Executor-compatible filtering
    happens in :func:`select_tools_for_files`.

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
    available_tools: dict[str, BaseToolPlugin] | None = None,
    auto_fix: bool = False,
) -> list[str]:
    """Return the union of tools relevant to a batch of changed files.

    After the file-pattern union, tools that the check/fmt executor would
    reject (advisory, pytest, non-fixable when ``auto_fix``) or that match
    every file via ``*`` are dropped unless the user named them in
    ``restrict_to``.

    Args:
        paths: Changed file paths.
        restrict_to: Optional user-supplied allowlist of tool names. When
            provided, the result is the intersection of the matched tools and
            this list (case-insensitive), preserving smart selection while
            honouring an explicit ``--tools`` filter.
        available_tools: Optional pre-fetched tool mapping (see
            :func:`get_tools_for_file`).
        auto_fix: When True, keep only tools that can format.

    Returns:
        Sorted list of unique tool names to run for this batch.
    """
    tools = available_tools if available_tools is not None else _all_tools()

    matched: set[str] = set()
    for path in paths:
        matched.update(get_tools_for_file(path, available_tools=tools))

    allowed: set[str] | None = None
    if restrict_to is not None:
        allowed = {name.lower() for name in restrict_to}
        matched = {name for name in matched if name.lower() in allowed}

    honor_enabled = restrict_to is None and available_tools is None
    selected: list[str] = []
    for name in matched:
        plugin = tools.get(name)
        if plugin is None:
            continue
        explicit = allowed is not None and name.lower() in allowed
        if _is_watch_compatible(
            name,
            plugin,
            auto_fix=auto_fix,
            explicit=explicit,
            honor_enabled=honor_enabled,
        ):
            selected.append(name)
    return sorted(selected)


def _all_tools() -> dict[str, BaseToolPlugin]:
    """Return the live tool registry mapping.

    Returns:
        Mapping of tool name to plugin instance.
    """
    return tool_manager.get_all_tools()

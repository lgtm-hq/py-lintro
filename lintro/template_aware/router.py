"""Router: match template paths to host linter tool names."""

from __future__ import annotations

import fnmatch
import os

from lintro.config.template_aware_config import TemplateAwareConfig


def match_pattern(path: str, pattern: str) -> bool:
    """Return whether a path matches a template glob pattern.

    Patterns are matched against the basename (e.g. ``main.py.jinja``) and
    against the full path with forward slashes for nested globs.

    Args:
        path: File path to test.
        pattern: Glob pattern from config (e.g. ``*.py.jinja``).

    Returns:
        True when the path matches the pattern.
    """
    basename = os.path.basename(path)
    if fnmatch.fnmatch(basename, pattern):
        return True
    normalized = path.replace("\\", "/")
    return fnmatch.fnmatch(normalized, pattern)


def resolve_tool_for_path(
    path: str,
    config: TemplateAwareConfig,
) -> str | None:
    """Resolve the host tool name for a template path via ``config.route``.

    Args:
        path: Absolute or relative template path.
        config: Template-aware configuration.

    Returns:
        Tool name (e.g. ``ruff``) or None when no route matches.
    """
    for pattern, tool_name in config.route.items():
        if match_pattern(path=path, pattern=pattern):
            return tool_name.lower()
    return None


def patterns_for_tool(
    tool_name: str,
    config: TemplateAwareConfig,
) -> list[str]:
    """Return template patterns routed to ``tool_name``.

    Args:
        tool_name: Host tool name (e.g. ``ruff``).
        config: Template-aware configuration.

    Returns:
        Patterns from ``config.patterns`` whose route target is ``tool_name``.
        Falls back to route keys when a routed pattern is absent from
        ``patterns`` so explicit routes still take effect.
    """
    tool_lower = tool_name.lower()
    pattern_set = set(config.patterns)
    matched: list[str] = []
    for pattern, routed_tool in config.route.items():
        if routed_tool.lower() != tool_lower:
            continue
        if pattern in pattern_set or not config.patterns:
            matched.append(pattern)
        elif pattern not in matched:
            # Route entries are authoritative even if omitted from patterns.
            matched.append(pattern)
    # Also include patterns listed without an explicit route when the pattern
    # appears in both lists with a matching route (already handled). Patterns
    # with no route are ignored — they cannot be dispatched.
    return matched


def is_template_path(path: str, config: TemplateAwareConfig) -> bool:
    """Return whether ``path`` matches any configured template pattern.

    Args:
        path: File path to test.
        config: Template-aware configuration.

    Returns:
        True when the path matches at least one pattern.
    """
    return any(match_pattern(path=path, pattern=pattern) for pattern in config.patterns)

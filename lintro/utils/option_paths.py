"""Keep a path that looks like an option from being read as one (#3584).

A relative path whose first character is ``-`` (``--config=x.py``, ``-e.sh``,
``-``, ``--``) is parsed by most tools as an option, a stdin marker or the end
of options, so a planted file could switch a tool's checks off. Every relative
path lintro hands to a tool goes through :func:`option_safe_path`, which
prefixes such a path with ``./``. Absolute paths and ordinary relative paths
are returned unchanged.

Tools echo the path they were given;
:func:`lintro.parsers.base_issue.strip_option_guard` removes the prefix again
where an issue records it, so users see ``-e.sh``, not ``./-e.sh``.
"""

from __future__ import annotations


def option_safe_path(path: str) -> str:
    """Return ``path`` in a form no tool can read as an option.

    Args:
        path: A path about to become a command-line argument.

    Returns:
        ``./<path>`` when ``path`` starts with ``-``, otherwise ``path``.
    """
    return f"./{path}" if path.startswith("-") else path


def option_safe_paths(paths: list[str]) -> list[str]:
    """Apply :func:`option_safe_path` to every path in a list.

    Args:
        paths: Paths about to become command-line arguments.

    Returns:
        The paths, each in a form no tool can read as an option.
    """
    return [option_safe_path(path) for path in paths]

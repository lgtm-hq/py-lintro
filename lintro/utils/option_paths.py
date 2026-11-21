"""Keep a file name from being read as anything but a file (#3584).

Tools read some leading characters of an argument specially: ``-`` starts an
option (``-`` alone is stdin, ``--`` ends options), ``@`` names an argument
file for tsc, vue-tsc, mypy, pytest and trufflehog, and markdownlint-cli2
skips a name starting with ``#``, ``!`` or ``:``. A planted file such as
``--config=x.py`` or ``@b.ts`` could therefore switch a tool's checks off.

The rule is an allow-list, so it fails closed for characters nobody has
checked yet: a relative path is handed over unchanged only when its first
character is a letter, a digit, or one of :data:`SAFE_FIRST_CHARACTERS`
(``.`` also covers ``./`` and ``../``). Any other relative path is handed over
as ``./<path>``. Absolute paths are never changed. Every ordinary file name,
including framework route files such as ``+page.svelte``, ``[slug].astro``,
``(group)/page.tsx`` and ``$id.tsx``, passes through as it is.

Tools echo the path they were given;
:func:`lintro.parsers.base_issue.strip_option_guard` removes the prefix again
where an issue records it, so users see ``@b.ts``, not ``./@b.ts``. The two
modules share :func:`starts_safely`'s rule, and a test pins that they agree.
"""

from __future__ import annotations

import os

#: First characters that no wrapped tool reads specially, besides letters and
#: digits. ``+ [ ( { $`` were checked against every installed wrapped tool
#: (#3584) and start common route-file names; ``[ ( {`` are glob characters
#: wherever they appear, so a ``./`` would not change how they are read.
SAFE_FIRST_CHARACTERS: frozenset[str] = frozenset("_.+[({$")


def starts_safely(path: str) -> bool:
    """Return whether ``path`` can be handed to a tool as it is.

    Args:
        path: A relative path.

    Returns:
        True when the first character is a letter, a digit or one of
        :data:`SAFE_FIRST_CHARACTERS`.
    """
    first = path[:1]
    return not first or first.isalnum() or first in SAFE_FIRST_CHARACTERS


def option_safe_path(path: str) -> str:
    """Return ``path`` in a form no tool can read as an option or argument file.

    Args:
        path: A path about to become a command-line argument.

    Returns:
        ``./<path>`` for a relative path that does not start safely,
        otherwise ``path`` unchanged.
    """
    if os.path.isabs(path) or starts_safely(path):
        return path
    return f"./{path}"


def option_safe_paths(paths: list[str]) -> list[str]:
    """Apply :func:`option_safe_path` to every path in a list.

    Args:
        paths: Paths about to become command-line arguments.

    Returns:
        The paths, each in a form no tool can read as an option.
    """
    return [option_safe_path(path) for path in paths]

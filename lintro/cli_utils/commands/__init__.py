"""CLI command modules for lintro.

Subcommand modules are imported on demand so ``import lintro.cli`` stays light.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lintro.cli_utils.commands.check import check_command as check_command
    from lintro.cli_utils.commands.completions import (
        completions_command as completions_command,
    )
    from lintro.cli_utils.commands.format import (
        format_code as format_code,
        format_code_legacy as format_code_legacy,
        format_command as format_command,
    )
    from lintro.cli_utils.commands.init import init_command as init_command
    from lintro.cli_utils.commands.list_tools import list_tools as list_tools

__all__ = [
    "check_command",
    "completions_command",
    "format_command",
    "format_code",
    "format_code_legacy",
    "init_command",
    "list_tools",
]

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "check_command": ("lintro.cli_utils.commands.check", "check_command"),
    "completions_command": (
        "lintro.cli_utils.commands.completions",
        "completions_command",
    ),
    "format_command": ("lintro.cli_utils.commands.format", "format_command"),
    "format_code": ("lintro.cli_utils.commands.format", "format_code"),
    "format_code_legacy": ("lintro.cli_utils.commands.format", "format_code_legacy"),
    "init_command": ("lintro.cli_utils.commands.init", "init_command"),
    "list_tools": ("lintro.cli_utils.commands.list_tools", "list_tools"),
}


def __getattr__(name: str) -> Any:
    """Resolve command exports on first access.

    Args:
        name: Attribute name being accessed.

    Returns:
        The lazily imported attribute.

    Raises:
        AttributeError: If ``name`` is not a public export.
    """
    if name not in _LAZY_EXPORTS:
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg)
    module_name, attr_name = _LAZY_EXPORTS[name]
    module = __import__(module_name, fromlist=[attr_name])
    value = getattr(module, attr_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Return available attributes including lazy exports.

    Returns:
        Sorted names from module globals and ``__all__``.
    """
    return sorted(set(globals()) | set(__all__))

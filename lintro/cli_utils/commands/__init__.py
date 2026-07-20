"""CLI command modules for lintro.

Subcommand modules are imported on demand so ``import lintro.cli`` stays light.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "check_command",  # noqa: F822 - resolved via module __getattr__
    "format_command",  # noqa: F822 - resolved via module __getattr__
    "format_code",  # noqa: F822 - resolved via module __getattr__
    "format_code_legacy",  # noqa: F822 - resolved via module __getattr__
    "init_command",  # noqa: F822 - resolved via module __getattr__
    "list_tools",  # noqa: F822 - resolved via module __getattr__
]

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "check_command": ("lintro.cli_utils.commands.check", "check_command"),
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

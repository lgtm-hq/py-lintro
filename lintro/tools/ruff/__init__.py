"""Ruff tool package.

Everything the ``ruff`` tool owns lives here: the plugin and its
:class:`~lintro.plugins.protocol.ToolDefinition` in
:mod:`lintro.tools.ruff.definition`, and the command builders and executors it
delegates to. Plugin discovery enters the package through that module (#2311).
"""

from lintro.tools.ruff.check import execute_ruff_check
from lintro.tools.ruff.commands import (
    build_ruff_check_command,
    build_ruff_format_command,
)
from lintro.tools.ruff.definition import RuffPlugin
from lintro.tools.ruff.fix import execute_ruff_fix

__all__ = [
    "RuffPlugin",
    "build_ruff_check_command",
    "build_ruff_format_command",
    "execute_ruff_check",
    "execute_ruff_fix",
]

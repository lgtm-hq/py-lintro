"""Dotenv-linter tool package.

Everything the ``dotenv-linter`` tool owns lives here: the plugin and its
:class:`~lintro.plugins.protocol.ToolDefinition` in
:mod:`lintro.tools.dotenv_linter.definition`. Plugin discovery enters the package
through that module (#2311).
"""

from lintro.tools.dotenv_linter.definition import (
    DOTENV_LINTER_DEFAULT_PRIORITY,
    DOTENV_LINTER_DEFAULT_TIMEOUT,
    DOTENV_LINTER_FILE_PATTERNS,
    DotenvLinterPlugin,
)

__all__ = [
    "DOTENV_LINTER_DEFAULT_PRIORITY",
    "DOTENV_LINTER_DEFAULT_TIMEOUT",
    "DOTENV_LINTER_FILE_PATTERNS",
    "DotenvLinterPlugin",
]

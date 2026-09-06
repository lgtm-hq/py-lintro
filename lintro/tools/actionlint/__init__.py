"""Actionlint tool package.

Everything the ``actionlint`` tool owns lives here: the plugin and its
:class:`~lintro.plugins.protocol.ToolDefinition` in
:mod:`lintro.tools.actionlint.definition`. Plugin discovery enters the package
through that module (#2311).
"""

from lintro.tools.actionlint.definition import (
    ACTIONLINT_DEFAULT_PRIORITY,
    ACTIONLINT_DEFAULT_TIMEOUT,
    ACTIONLINT_FILE_PATTERNS,
    ActionlintPlugin,
)

__all__ = [
    "ACTIONLINT_DEFAULT_PRIORITY",
    "ACTIONLINT_DEFAULT_TIMEOUT",
    "ACTIONLINT_FILE_PATTERNS",
    "ActionlintPlugin",
]

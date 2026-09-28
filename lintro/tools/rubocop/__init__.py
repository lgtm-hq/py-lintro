"""RuboCop tool package.

Everything the ``rubocop`` tool owns lives here: the plugin and its
:class:`~lintro.plugins.protocol.ToolDefinition` in
:mod:`lintro.tools.rubocop.definition`. Plugin discovery enters the package
through that module (#2311).
"""

from lintro.tools.rubocop.definition import (
    RUBOCOP_DEFAULT_TIMEOUT,
    RUBOCOP_FILE_PATTERNS,
    RubocopPlugin,
)

__all__ = [
    "RUBOCOP_DEFAULT_TIMEOUT",
    "RUBOCOP_FILE_PATTERNS",
    "RubocopPlugin",
]

"""Commitlint tool package.

Everything the ``commitlint`` tool owns lives here: the plugin and its
:class:`~lintro.plugins.protocol.ToolDefinition` in
:mod:`lintro.tools.commitlint.definition`. ``lintro.tools.definitions.commitlint``
re-exports the plugin so plugin discovery keeps finding it (#2311).
"""

from lintro.tools.commitlint.definition import (
    COMMITLINT_CONFIG_MISSING_EXIT,
    COMMITLINT_DEFAULT_PRIORITY,
    COMMITLINT_DEFAULT_TIMEOUT,
    COMMITLINT_FILE_PATTERNS,
    CommitlintPlugin,
)

__all__ = [
    "COMMITLINT_CONFIG_MISSING_EXIT",
    "COMMITLINT_DEFAULT_PRIORITY",
    "COMMITLINT_DEFAULT_TIMEOUT",
    "COMMITLINT_FILE_PATTERNS",
    "CommitlintPlugin",
]

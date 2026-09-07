"""Golangci-lint tool package.

Everything the ``golangci-lint`` tool owns lives here: the plugin and its
:class:`~lintro.plugins.protocol.ToolDefinition` in
:mod:`lintro.tools.golangci_lint.definition`. Plugin discovery enters the package
through that module (#2311).
"""

from lintro.tools.golangci_lint.definition import (
    GOLANGCI_LINT_DEFAULT_PRIORITY,
    GOLANGCI_LINT_DEFAULT_TIMEOUT,
    GOLANGCI_LINT_FILE_PATTERNS,
    GolangciLintPlugin,
)

__all__ = [
    "GOLANGCI_LINT_DEFAULT_PRIORITY",
    "GOLANGCI_LINT_DEFAULT_TIMEOUT",
    "GOLANGCI_LINT_FILE_PATTERNS",
    "GolangciLintPlugin",
]

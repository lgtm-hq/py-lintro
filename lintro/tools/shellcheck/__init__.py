"""Shellcheck tool package.

Everything the ``shellcheck`` tool owns lives here: the plugin and its
:class:`~lintro.plugins.protocol.ToolDefinition` in
:mod:`lintro.tools.shellcheck.definition`. Plugin discovery enters the package
through that module (#2311).
"""

from lintro.tools.shellcheck.definition import (
    SHELLCHECK_DEFAULT_FORMAT,
    SHELLCHECK_DEFAULT_PRIORITY,
    SHELLCHECK_DEFAULT_SEVERITY,
    SHELLCHECK_DEFAULT_TIMEOUT,
    SHELLCHECK_FILE_PATTERNS,
    SHELLCHECK_SEVERITY_LEVELS,
    SHELLCHECK_SHELL_DIALECTS,
    ShellcheckPlugin,
    normalize_shellcheck_severity,
    normalize_shellcheck_shell,
)

__all__ = [
    "SHELLCHECK_DEFAULT_FORMAT",
    "SHELLCHECK_DEFAULT_PRIORITY",
    "SHELLCHECK_DEFAULT_SEVERITY",
    "SHELLCHECK_DEFAULT_TIMEOUT",
    "SHELLCHECK_FILE_PATTERNS",
    "SHELLCHECK_SEVERITY_LEVELS",
    "SHELLCHECK_SHELL_DIALECTS",
    "ShellcheckPlugin",
    "normalize_shellcheck_severity",
    "normalize_shellcheck_shell",
]

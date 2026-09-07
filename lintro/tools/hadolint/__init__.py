"""Hadolint tool package.

Everything the ``hadolint`` tool owns lives here: the plugin and its
:class:`~lintro.plugins.protocol.ToolDefinition` in
:mod:`lintro.tools.hadolint.definition`. Plugin discovery enters the package
through that module (#2311).
"""

from lintro.tools.hadolint.definition import (
    HADOLINT_DEFAULT_FAILURE_THRESHOLD,
    HADOLINT_DEFAULT_FORMAT,
    HADOLINT_DEFAULT_NO_COLOR,
    HADOLINT_DEFAULT_PRIORITY,
    HADOLINT_DEFAULT_TIMEOUT,
    HADOLINT_FILE_PATTERNS,
    HadolintPlugin,
)

__all__ = [
    "HADOLINT_DEFAULT_FAILURE_THRESHOLD",
    "HADOLINT_DEFAULT_FORMAT",
    "HADOLINT_DEFAULT_NO_COLOR",
    "HADOLINT_DEFAULT_PRIORITY",
    "HADOLINT_DEFAULT_TIMEOUT",
    "HADOLINT_FILE_PATTERNS",
    "HadolintPlugin",
]

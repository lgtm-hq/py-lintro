"""Gitleaks tool package.

Everything the ``gitleaks`` tool owns lives here: the plugin and its
:class:`~lintro.plugins.protocol.ToolDefinition` in
:mod:`lintro.tools.gitleaks.definition`. Plugin discovery enters the package
through that module (#2311).
"""

from lintro.tools.gitleaks.definition import (
    GITLEAKS_DEFAULT_PRIORITY,
    GITLEAKS_DEFAULT_TIMEOUT,
    GITLEAKS_FILE_PATTERNS,
    GITLEAKS_OUTPUT_FORMAT,
    GitleaksPlugin,
)

__all__ = [
    "GITLEAKS_DEFAULT_PRIORITY",
    "GITLEAKS_DEFAULT_TIMEOUT",
    "GITLEAKS_FILE_PATTERNS",
    "GITLEAKS_OUTPUT_FORMAT",
    "GitleaksPlugin",
]

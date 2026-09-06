"""Semgrep tool package.

Everything the ``semgrep`` tool owns lives here: the plugin and its
:class:`~lintro.plugins.protocol.ToolDefinition` in
:mod:`lintro.tools.semgrep.definition`. Plugin discovery enters the package
through that module (#2311).
"""

from lintro.tools.semgrep.definition import (
    SEMGREP_DEFAULT_CONFIG,
    SEMGREP_DEFAULT_PRIORITY,
    SEMGREP_DEFAULT_TIMEOUT,
    SEMGREP_FILE_PATTERNS,
    SEMGREP_OUTPUT_FORMAT,
    SemgrepPlugin,
)

__all__ = [
    "SEMGREP_DEFAULT_CONFIG",
    "SEMGREP_DEFAULT_PRIORITY",
    "SEMGREP_DEFAULT_TIMEOUT",
    "SEMGREP_FILE_PATTERNS",
    "SEMGREP_OUTPUT_FORMAT",
    "SemgrepPlugin",
]

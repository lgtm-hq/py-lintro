"""TruffleHog tool package.

Everything the ``trufflehog`` tool owns lives here: the plugin and its
:class:`~lintro.plugins.protocol.ToolDefinition` in
:mod:`lintro.tools.trufflehog.definition`. ``lintro.tools.definitions.trufflehog``
re-exports the plugin so plugin discovery keeps finding it (#2311).
"""

from lintro.tools.trufflehog.definition import (
    TRUFFLEHOG_DEFAULT_PRIORITY,
    TRUFFLEHOG_DEFAULT_TIMEOUT,
    TRUFFLEHOG_FILE_PATTERNS,
    TrufflehogPlugin,
)

__all__ = [
    "TRUFFLEHOG_DEFAULT_PRIORITY",
    "TRUFFLEHOG_DEFAULT_TIMEOUT",
    "TRUFFLEHOG_FILE_PATTERNS",
    "TrufflehogPlugin",
]

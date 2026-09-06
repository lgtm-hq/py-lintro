"""Spectral tool package.

Everything the ``spectral`` tool owns lives here: the plugin and its
:class:`~lintro.plugins.protocol.ToolDefinition` in
:mod:`lintro.tools.spectral.definition`. ``lintro.tools.definitions.spectral``
re-exports the plugin so plugin discovery keeps finding it (#2311).
"""

from lintro.tools.spectral.definition import (
    SPECTRAL_DEFAULT_PRIORITY,
    SPECTRAL_DEFAULT_TIMEOUT,
    SPECTRAL_FILE_PATTERNS,
    SPECTRAL_RULESET_FILES,
    SpectralPlugin,
)

__all__ = [
    "SPECTRAL_DEFAULT_PRIORITY",
    "SPECTRAL_DEFAULT_TIMEOUT",
    "SPECTRAL_FILE_PATTERNS",
    "SPECTRAL_RULESET_FILES",
    "SpectralPlugin",
]

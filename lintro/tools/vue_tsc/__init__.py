"""Vue-tsc tool package.

Everything the ``vue-tsc`` tool owns lives here: the plugin and its
:class:`~lintro.plugins.protocol.ToolDefinition` in
:mod:`lintro.tools.vue_tsc.definition`. Plugin discovery enters the package
through that module (#2311).
"""

from lintro.tools.vue_tsc.definition import (
    VUE_TSC_DEFAULT_PRIORITY,
    VUE_TSC_DEFAULT_TIMEOUT,
    VUE_TSC_FILE_PATTERNS,
    VueTscPlugin,
)

__all__ = [
    "VUE_TSC_DEFAULT_PRIORITY",
    "VUE_TSC_DEFAULT_TIMEOUT",
    "VUE_TSC_FILE_PATTERNS",
    "VueTscPlugin",
]

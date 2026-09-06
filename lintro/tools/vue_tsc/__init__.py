"""Vue-tsc tool package.

Everything the ``vue-tsc`` tool owns lives here: the plugin and its
:class:`~lintro.plugins.protocol.ToolDefinition` in
:mod:`lintro.tools.vue_tsc.definition`. ``lintro.tools.definitions.vue_tsc``
re-exports the plugin so plugin discovery keeps finding it (#2311).
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

"""Re-export shim for the vue-tsc tool definition (#2311).

The vue-tsc plugin now lives in its own package,
:mod:`lintro.tools.vue_tsc`. Plugin discovery still scans this package, so
importing this module registers the tool. Deleted once discovery moves
to the per-tool packages.
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

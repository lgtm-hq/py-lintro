"""Re-export shim for the svelte-check tool definition (#2311).

The svelte-check plugin now lives in its own package,
:mod:`lintro.tools.svelte_check`. Plugin discovery still scans this package, so
importing this module registers the tool. Deleted once discovery moves
to the per-tool packages.
"""

from lintro.tools.svelte_check.definition import (
    SVELTE_CHECK_DEFAULT_PRIORITY,
    SVELTE_CHECK_DEFAULT_TIMEOUT,
    SVELTE_CHECK_FILE_PATTERNS,
    SvelteCheckPlugin,
)

__all__ = [
    "SVELTE_CHECK_DEFAULT_PRIORITY",
    "SVELTE_CHECK_DEFAULT_TIMEOUT",
    "SVELTE_CHECK_FILE_PATTERNS",
    "SvelteCheckPlugin",
]

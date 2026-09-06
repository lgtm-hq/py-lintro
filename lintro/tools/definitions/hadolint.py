"""Re-export shim for the hadolint tool definition (#2311).

The hadolint plugin now lives in its own package,
:mod:`lintro.tools.hadolint`. Plugin discovery still scans this package, so
importing this module registers the tool. Deleted once discovery moves
to the per-tool packages.
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

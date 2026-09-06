"""Re-export shim for the shellcheck tool definition (#2311).

The shellcheck plugin now lives in its own package,
:mod:`lintro.tools.shellcheck`. Plugin discovery still scans this package, so
importing this module registers the tool. Deleted once discovery moves
to the per-tool packages.
"""

from lintro.tools.shellcheck.definition import (
    SHELLCHECK_DEFAULT_FORMAT,
    SHELLCHECK_DEFAULT_PRIORITY,
    SHELLCHECK_DEFAULT_SEVERITY,
    SHELLCHECK_DEFAULT_TIMEOUT,
    SHELLCHECK_FILE_PATTERNS,
    SHELLCHECK_SEVERITY_LEVELS,
    SHELLCHECK_SHELL_DIALECTS,
    ShellcheckPlugin,
    normalize_shellcheck_severity,
    normalize_shellcheck_shell,
)

__all__ = [
    "SHELLCHECK_DEFAULT_FORMAT",
    "SHELLCHECK_DEFAULT_PRIORITY",
    "SHELLCHECK_DEFAULT_SEVERITY",
    "SHELLCHECK_DEFAULT_TIMEOUT",
    "SHELLCHECK_FILE_PATTERNS",
    "SHELLCHECK_SEVERITY_LEVELS",
    "SHELLCHECK_SHELL_DIALECTS",
    "ShellcheckPlugin",
    "normalize_shellcheck_severity",
    "normalize_shellcheck_shell",
]

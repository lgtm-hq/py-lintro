"""Re-export shim for the golangci-lint tool definition (#2311).

The golangci-lint plugin now lives in its own package,
:mod:`lintro.tools.golangci_lint`. Plugin discovery still scans this package, so
importing this module registers the tool. Deleted once discovery moves
to the per-tool packages.
"""

from lintro.tools.golangci_lint.definition import (
    GOLANGCI_LINT_DEFAULT_PRIORITY,
    GOLANGCI_LINT_DEFAULT_TIMEOUT,
    GOLANGCI_LINT_FILE_PATTERNS,
    GolangciLintPlugin,
)

__all__ = [
    "GOLANGCI_LINT_DEFAULT_PRIORITY",
    "GOLANGCI_LINT_DEFAULT_TIMEOUT",
    "GOLANGCI_LINT_FILE_PATTERNS",
    "GolangciLintPlugin",
]

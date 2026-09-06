"""Re-export shim for the oxlint type-aware doctor checks (#2311).

The checks now live in the oxlint package, :mod:`lintro.tools.oxlint.doctor`.
Plugin discovery still scans this package and imports this module, so the shim
keeps the builtin module index unchanged. Deleted once discovery moves to the
per-tool packages.
"""

from lintro.tools.oxlint.doctor import (
    OxlintCheckResult,
    check_oxlint_type_aware,
    oxlintrc_type_aware_enabled,
)

__all__ = [
    "OxlintCheckResult",
    "check_oxlint_type_aware",
    "oxlintrc_type_aware_enabled",
]

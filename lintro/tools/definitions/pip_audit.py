"""Re-export shim for the pip_audit tool definition (#2311).

The pip_audit plugin now lives in its own package,
:mod:`lintro.tools.pip_audit`. Plugin discovery still scans this package, so
importing this module registers the tool. Deleted once discovery moves
to the per-tool packages.
"""

from lintro.tools.pip_audit.definition import (
    PIP_AUDIT_DEFAULT_PRIORITY,
    PIP_AUDIT_DEFAULT_TIMEOUT,
    PIP_AUDIT_FILE_PATTERNS,
    PIP_AUDIT_PROJECT_FILES,
    PIP_AUDIT_REQUIREMENTS_DIR,
    PIP_AUDIT_REQUIREMENTS_GLOB,
    PipAuditPlugin,
)

__all__ = [
    "PIP_AUDIT_DEFAULT_PRIORITY",
    "PIP_AUDIT_DEFAULT_TIMEOUT",
    "PIP_AUDIT_FILE_PATTERNS",
    "PIP_AUDIT_PROJECT_FILES",
    "PIP_AUDIT_REQUIREMENTS_DIR",
    "PIP_AUDIT_REQUIREMENTS_GLOB",
    "PipAuditPlugin",
]

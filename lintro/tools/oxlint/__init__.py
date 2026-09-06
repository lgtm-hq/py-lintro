"""Oxlint tool package.

Everything the ``oxlint`` tool owns lives here: the plugin and its
:class:`~lintro.plugins.protocol.ToolDefinition` in
:mod:`lintro.tools.oxlint.definition`, and the type-aware doctor checks in
:mod:`lintro.tools.oxlint.doctor`. ``lintro.tools.definitions.oxlint`` and
``lintro.tools.definitions.oxlint_doctor`` re-export them so plugin discovery
keeps finding both (#2311).
"""

from lintro.tools.oxlint.definition import (
    OXLINT_DEFAULT_PRIORITY,
    OXLINT_DEFAULT_TIMEOUT,
    OXLINT_FILE_PATTERNS,
    OxlintPlugin,
)
from lintro.tools.oxlint.doctor import (
    OxlintCheckResult,
    check_oxlint_type_aware,
    oxlintrc_type_aware_enabled,
)

__all__ = [
    "OXLINT_DEFAULT_PRIORITY",
    "OXLINT_DEFAULT_TIMEOUT",
    "OXLINT_FILE_PATTERNS",
    "OxlintCheckResult",
    "OxlintPlugin",
    "check_oxlint_type_aware",
    "oxlintrc_type_aware_enabled",
]

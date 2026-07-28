"""Core SARIF v2.1.0 output package.

SARIF is a first-class lintro output format (``--output-format sarif``) and
works with the AI layer fully disabled. Nothing in this package imports
:mod:`lintro.ai`: :mod:`~lintro.utils.output.sarif.document` accepts already
built AI objects via its ``ai_suggestions``/``ai_summary`` keywords, and
callers that want enrichment obtain them from :mod:`lintro.ai.sarif_bridge`
through the injected
:class:`~lintro.models.core.ai_seam.AISarifEnricher` seam (issue #724).
"""

from lintro.utils.output.sarif.bridge import standard_issues_from_results
from lintro.utils.output.sarif.document import (
    StandardIssue,
    render_fixes_sarif,
    to_sarif,
    write_sarif,
)

__all__ = [
    "StandardIssue",
    "render_fixes_sarif",
    "standard_issues_from_results",
    "to_sarif",
    "write_sarif",
]

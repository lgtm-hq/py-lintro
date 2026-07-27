"""Core SARIF v2.1.0 output package.

SARIF is a first-class lintro output format (``--output-format sarif``) and
works with the AI layer fully disabled. AI enrichment is optional and is
injected by callers, so nothing in this package imports :mod:`lintro.ai` at
runtime.
"""

from lintro.utils.output.sarif.bridge import (
    standard_issues_from_results,
    suggestions_from_results,
    summary_from_results,
)
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
    "suggestions_from_results",
    "summary_from_results",
    "to_sarif",
    "write_sarif",
]

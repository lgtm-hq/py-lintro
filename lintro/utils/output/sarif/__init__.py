"""Core SARIF v2.1.0 output package.

SARIF is a first-class lintro output format (``--output-format sarif``) and
works with the AI layer fully disabled. AI enrichment is optional and is
injected by callers: :mod:`lintro.utils.output.sarif.document` renders it
without importing :mod:`lintro.ai` at all, and
:mod:`lintro.utils.output.sarif.bridge` imports
:mod:`lintro.ai.models` only when it has enrichment metadata to
reconstruct. A standard-only render therefore never resolves an AI model
through this package.
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

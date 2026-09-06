"""Re-export shim for the idiom-review tool definition (#2311).

The idiom-review plugin now lives in its own package,
:mod:`lintro.tools.idiom_review`. Plugin discovery still scans this package, so
importing this module registers the tool. Deleted once discovery moves
to the per-tool packages.
"""

from lintro.tools.idiom_review.definition import (
    IDIOM_REVIEW_DEFAULT_MAX_FILES,
    IDIOM_REVIEW_DEFAULT_TIMEOUT,
    IDIOM_REVIEW_FILE_PATTERNS,
    IDIOM_REVIEW_PRIORITY,
    IDIOM_REVIEW_TOOL_NAME,
    IdiomReviewPlugin,
)

__all__ = [
    "IDIOM_REVIEW_DEFAULT_MAX_FILES",
    "IDIOM_REVIEW_DEFAULT_TIMEOUT",
    "IDIOM_REVIEW_FILE_PATTERNS",
    "IDIOM_REVIEW_PRIORITY",
    "IDIOM_REVIEW_TOOL_NAME",
    "IdiomReviewPlugin",
]

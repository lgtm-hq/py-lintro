"""Built-in review checklist items (Tier 1 and Tier 2).

.. deprecated::
    The checklist corpus has been externalized into versioned YAML under
    :mod:`lintro.ai.review.checklist` (see issue #1031). This module is kept as
    a thin re-export shim so existing imports of ``BUILTIN_CHECKLIST_ITEMS`` and
    the tier splits keep working; new code should import from
    :mod:`lintro.ai.review.checklist` instead.
"""

from __future__ import annotations

from lintro.ai.review.checklist import (
    BUILTIN_CHECKLIST_ITEMS,
    TIER1_CHECKLIST_ITEMS,
    TIER2_CHECKLIST_ITEMS,
    load_builtin_checklist,
)

__all__ = [
    "BUILTIN_CHECKLIST_ITEMS",
    "TIER1_CHECKLIST_ITEMS",
    "TIER2_CHECKLIST_ITEMS",
    "load_builtin_checklist",
]

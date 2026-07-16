"""Built-in review checklist corpus package.

The checklist corpus lives as versioned YAML rows under ``corpus/`` and is
parsed by :mod:`lintro.ai.review.checklist.loader`. This package is the public
entry point: it exposes the same registry API historically provided by
``lintro.ai.review.checklist_builtin`` — :data:`BUILTIN_CHECKLIST_ITEMS` and the
Tier 1 / Tier 2 splits — plus :func:`load_builtin_checklist` for callers that
want to reload the corpus explicitly.
"""

from __future__ import annotations

from lintro.ai.review.checklist.loader import load_builtin_checklist
from lintro.ai.review.models.checklist_item import ChecklistItem

__all__ = [
    "BUILTIN_CHECKLIST_ITEMS",
    "TIER1_CHECKLIST_ITEMS",
    "TIER2_CHECKLIST_ITEMS",
    "load_builtin_checklist",
]

BUILTIN_CHECKLIST_ITEMS: tuple[ChecklistItem, ...] = load_builtin_checklist()

TIER1_CHECKLIST_ITEMS: tuple[ChecklistItem, ...] = tuple(
    item for item in BUILTIN_CHECKLIST_ITEMS if item.tier == 1
)

TIER2_CHECKLIST_ITEMS: tuple[ChecklistItem, ...] = tuple(
    item for item in BUILTIN_CHECKLIST_ITEMS if item.tier == 2
)

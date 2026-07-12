"""Checklist registry loader and validation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lintro.ai.review.checklist import BUILTIN_CHECKLIST_ITEMS
from lintro.ai.review.constants import (
    CUSTOM_CHECKLIST_ID_START,
    TIER1_CHECKLIST_ID_END,
    TIER1_CHECKLIST_ID_START,
    TIER2_CHECKLIST_ID_START,
)
from lintro.ai.review.models.checklist_item import ChecklistItem

if TYPE_CHECKING:
    from lintro.config.lintro_config import LintroConfig

__all__ = [
    "get_all_checklist_items",
    "validate_checklist_items",
]


def get_all_checklist_items(
    *,
    config: LintroConfig | None = None,
) -> list[ChecklistItem]:
    """Return builtin and custom checklist items from configuration.

    Args:
        config: Loaded Lintro configuration. When omitted, only builtin items
            are returned.

    Returns:
        Combined checklist items with custom config entries appended.
    """
    items = list(BUILTIN_CHECKLIST_ITEMS)
    if config is None:
        return items

    next_custom_id = CUSTOM_CHECKLIST_ID_START
    for custom_item in config.review.checklist.items:
        items.append(
            ChecklistItem(
                id=next_custom_id,
                question=custom_item.question,
                domains=tuple(custom_item.domains),
                languages=tuple(custom_item.languages),
                category=custom_item.category,
                tier=2,
            ),
        )
        next_custom_id += 1

    validate_checklist_items(items=items)
    return items


def validate_checklist_items(*, items: list[ChecklistItem]) -> None:
    """Validate checklist registry invariants.

    Args:
        items: Checklist items to validate.

    Raises:
        ValueError: When ids or questions are invalid.
    """
    seen_ids: set[int] = set()
    seen_questions: set[str] = set()
    for item in items:
        if item.id in seen_ids:
            msg = f"Duplicate checklist id: {item.id}"
            raise ValueError(msg)
        seen_ids.add(item.id)

        normalized_question = " ".join(item.question.split()).casefold()
        if normalized_question in seen_questions:
            msg = f"Duplicate checklist question: {item.id}"
            raise ValueError(msg)
        seen_questions.add(normalized_question)

        if not item.question.strip():
            msg = f"Checklist item {item.id} has an empty question"
            raise ValueError(msg)

        if item.tier not in {1, 2}:
            msg = f"Checklist item {item.id} has invalid tier: {item.tier}"
            raise ValueError(msg)

        if item.tier == 1 and not (
            TIER1_CHECKLIST_ID_START <= item.id <= TIER1_CHECKLIST_ID_END
        ):
            msg = (
                f"Tier 1 checklist item {item.id} must use id "
                f"{TIER1_CHECKLIST_ID_START}-{TIER1_CHECKLIST_ID_END}"
            )
            raise ValueError(msg)

        if item.tier == 2 and item.id < TIER2_CHECKLIST_ID_START:
            msg = (
                f"Tier 2 checklist item {item.id} must use id "
                f">= {TIER2_CHECKLIST_ID_START}"
            )
            raise ValueError(msg)

        if item.tier == 1 and (item.domains or item.languages):
            msg = (
                f"Tier 1 checklist item {item.id} must have empty domains "
                "and languages"
            )
            raise ValueError(msg)


def _validate_builtin_checklist_items() -> None:
    """Validate builtin checklist invariants once at import time."""
    validate_checklist_items(items=list(BUILTIN_CHECKLIST_ITEMS))


_validate_builtin_checklist_items()

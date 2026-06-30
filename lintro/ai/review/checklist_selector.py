"""Checklist item selection and prompt formatting."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lintro.ai.review.file_language import languages_for_path, languages_for_paths

if TYPE_CHECKING:
    from lintro.ai.review.models.checklist_item import ChecklistItem
    from lintro.ai.review.models.file_classification import FileClassification

__all__ = [
    "format_checklist_for_prompt",
    "select_checklist_items",
]


def select_checklist_items(
    *,
    classifications: list[FileClassification],
    items: list[ChecklistItem],
) -> list[ChecklistItem]:
    """Select checklist items for the changed files in a review diff.

    Tier 1 items are always included. A Tier 2 item is included when its role
    domains intersect the domains present in the diff and/or its languages
    intersect the languages present in the diff. When an item defines both
    axes, at least one changed file must satisfy both on that file. A Tier 2
    item with neither axis set is universal and included whenever the diff
    has at least one file.

    Args:
        classifications: Per-file domain classifications for the review diff.
            Each classification also carries the file path used to derive
            language tags.
        items: Full checklist registry (builtin plus custom config items).

    Returns:
        Selected items sorted by stable checklist id.
    """
    has_files = bool(classifications)

    selected: list[ChecklistItem] = []
    for item in items:
        if item.tier == 1:
            selected.append(item)
            continue
        if _item_matches_diff(
            item=item,
            classifications=classifications,
            has_files=has_files,
        ):
            selected.append(item)

    return sorted(selected, key=lambda checklist_item: checklist_item.id)


def _item_matches_diff(
    *,
    item: ChecklistItem,
    classifications: list[FileClassification],
    has_files: bool,
) -> bool:
    """Return True when a Tier 2 item activates for the diff.

    Args:
        item: Checklist item to evaluate.
        classifications: Per-file domain classifications for the review diff.
        has_files: Whether the diff has at least one changed file.

    Returns:
        True when the item should be selected.
    """
    if not item.domains and not item.languages:
        return has_files

    if item.domains and item.languages:
        return _dual_axis_matches_any_file(
            item=item,
            classifications=classifications,
        )

    present_domains = {
        domain
        for classification in classifications
        for domain in classification.domains
    }
    present_languages = languages_for_paths(
        paths=[classification.path for classification in classifications],
    )
    if item.domains:
        return bool(present_domains.intersection(item.domains))
    return bool(present_languages.intersection(item.languages))


def _dual_axis_matches_any_file(
    *,
    item: ChecklistItem,
    classifications: list[FileClassification],
) -> bool:
    """Return True when at least one changed file satisfies both axes.

    Args:
        item: Checklist item with both ``domains`` and ``languages`` set.
        classifications: Per-file domain classifications for the review diff.

    Returns:
        True when some file matches both the domain and language axes.
    """
    for classification in classifications:
        file_domains = set(classification.domains)
        file_languages = languages_for_path(path=classification.path)
        domain_match = bool(file_domains.intersection(item.domains))
        language_match = bool(file_languages.intersection(item.languages))
        if domain_match and language_match:
            return True
    return False


def format_checklist_for_prompt(
    *,
    items: list[ChecklistItem],
) -> tuple[str, dict[int, int]]:
    """Format selected checklist items for the review prompt.

    Renumbers items sequentially (1..N) in prompt order while preserving the
    mapping back to stable checklist ids for finding attribution.

    Args:
        items: Selected checklist items sorted by id.

    Returns:
        Tuple of formatted prompt text and prompt-id to checklist-id mapping.
    """
    lines: list[str] = []
    prompt_to_checklist: dict[int, int] = {}

    for prompt_id, item in enumerate(items, start=1):
        prompt_to_checklist[prompt_id] = item.id
        normalized_question = " ".join(item.question.split())
        lines.append(
            f"{prompt_id}. [{item.category.value}] {normalized_question}",
        )

    return "\n".join(lines), prompt_to_checklist

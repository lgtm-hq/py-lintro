"""Loader for the externalized built-in review checklist corpus.

The built-in checklist items are stored as versioned YAML rows under
``corpus/`` rather than inline Python declarations. This keeps the corpus a
data artifact (small, reviewable diffs; editable without Python fluency) while
preserving the public registry API exposed by
:mod:`lintro.ai.review.checklist`.

Rows are parsed into
:class:`~lintro.ai.review.models.checklist_item.ChecklistItem` instances and
validated at load time (fail fast) against the invariants owned by
:mod:`lintro.ai.review.constants`: unique ids, unique normalized question text,
tier/id ranges, empty axes for Tier 1, and known ``FileDomain`` / ``identify``
language tags.
"""

from __future__ import annotations

# nosemgrep: python.lang.compatibility.python37.python37-compatibility-importlib2
from importlib import resources
from typing import Any

import yaml
from identify.identify import ALL_TAGS

from lintro.ai.review.constants import (
    TIER1_CHECKLIST_ID_END,
    TIER1_CHECKLIST_ID_START,
    TIER2_CHECKLIST_ID_START,
)
from lintro.ai.review.enums.file_domain import FileDomain
from lintro.ai.review.enums.review_category import ReviewCategory
from lintro.ai.review.models.checklist_item import ChecklistItem

__all__ = ["load_builtin_checklist"]

# Corpus YAML files, loaded in order. Tier 1 first so ids stay ascending.
_CORPUS_FILES: tuple[str, ...] = ("tier1.yaml", "tier2.yaml")

_REQUIRED_FIELDS: frozenset[str] = frozenset(
    {"id", "tier", "category", "question", "domains", "languages"},
)

_KNOWN_LANGUAGES: frozenset[str] = frozenset(ALL_TAGS)


def _read_corpus_rows() -> list[dict[str, Any]]:
    """Read and concatenate every corpus YAML file into raw rows.

    Returns:
        list[dict[str, Any]]: Raw mapping rows in corpus-file order.

    Raises:
        ValueError: When a corpus file is not a list of mapping rows.
    """
    package = resources.files(__package__).joinpath("corpus")
    rows: list[dict[str, Any]] = []
    for file_name in _CORPUS_FILES:
        text = package.joinpath(file_name).read_text(encoding="utf-8")
        parsed = yaml.safe_load(text)
        if not isinstance(parsed, list):
            msg = f"Checklist corpus {file_name} must be a list of rows"
            raise ValueError(msg)
        for row in parsed:
            if not isinstance(row, dict):
                msg = f"Checklist corpus {file_name} has a non-mapping row: {row!r}"
                raise ValueError(msg)
            rows.append(row)
    return rows


def _parse_row(*, row: dict[str, Any]) -> ChecklistItem:
    """Parse a single corpus row into a :class:`ChecklistItem`.

    Args:
        row: Raw mapping row from a corpus YAML file.

    Returns:
        ChecklistItem: The parsed checklist item.

    Raises:
        ValueError: When required fields are missing, unexpected fields are
            present, or a domain/language/category tag is invalid.
    """
    keys = set(row)
    missing = _REQUIRED_FIELDS - keys
    if missing:
        msg = f"Checklist row {row.get('id')!r} missing fields: {sorted(missing)}"
        raise ValueError(msg)
    unexpected = keys - _REQUIRED_FIELDS
    if unexpected:
        msg = (
            f"Checklist row {row.get('id')!r} has unexpected fields: "
            f"{sorted(unexpected)}"
        )
        raise ValueError(msg)

    try:
        category = ReviewCategory(row["category"])
    except ValueError as error:
        msg = (
            f"Checklist row {row.get('id')!r} has invalid category: {row['category']!r}"
        )
        raise ValueError(msg) from error

    domains: list[FileDomain] = []
    for domain in row["domains"] or ():
        try:
            domains.append(FileDomain(domain))
        except ValueError as error:
            msg = f"Checklist row {row.get('id')!r} has invalid domain: {domain!r}"
            raise ValueError(msg) from error

    languages = tuple(row["languages"] or ())
    unknown = set(languages) - _KNOWN_LANGUAGES
    if unknown:
        msg = (
            f"Checklist row {row.get('id')!r} has unknown language tags: "
            f"{sorted(unknown)}"
        )
        raise ValueError(msg)

    return ChecklistItem(
        id=row["id"],
        question=row["question"],
        domains=tuple(domains),
        languages=languages,
        category=category,
        tier=row["tier"],
    )


def _validate_corpus(*, items: tuple[ChecklistItem, ...]) -> None:
    """Validate corpus-level invariants (fail fast at load time).

    Args:
        items: Parsed checklist items.

    Raises:
        ValueError: When ids, question text, or tier/id ranges are invalid.
    """
    seen_ids: set[int] = set()
    seen_questions: set[str] = set()
    for item in items:
        if item.id in seen_ids:
            msg = f"Duplicate checklist id in corpus: {item.id}"
            raise ValueError(msg)
        seen_ids.add(item.id)

        normalized = " ".join(item.question.split()).casefold()
        if normalized in seen_questions:
            msg = f"Duplicate checklist question in corpus: {item.id}"
            raise ValueError(msg)
        seen_questions.add(normalized)

        if not item.question.strip():
            msg = f"Checklist item {item.id} has an empty question"
            raise ValueError(msg)

        if item.tier not in {1, 2}:
            msg = f"Checklist item {item.id} has invalid tier: {item.tier}"
            raise ValueError(msg)

        if item.tier == 1:
            if not TIER1_CHECKLIST_ID_START <= item.id <= TIER1_CHECKLIST_ID_END:
                msg = (
                    f"Tier 1 checklist item {item.id} must use id "
                    f"{TIER1_CHECKLIST_ID_START}-{TIER1_CHECKLIST_ID_END}"
                )
                raise ValueError(msg)
            if item.domains or item.languages:
                msg = (
                    f"Tier 1 checklist item {item.id} must have empty domains "
                    "and languages"
                )
                raise ValueError(msg)

        if item.tier == 2 and item.id < TIER2_CHECKLIST_ID_START:
            msg = (
                f"Tier 2 checklist item {item.id} must use id "
                f">= {TIER2_CHECKLIST_ID_START}"
            )
            raise ValueError(msg)


def load_builtin_checklist() -> tuple[ChecklistItem, ...]:
    """Load and validate the built-in checklist corpus from packaged YAML.

    Returns:
        tuple[ChecklistItem, ...]: All built-in checklist items in corpus order
        (Tier 1 first, then Tier 2), validated for the registry invariants.
    """
    items = tuple(_parse_row(row=row) for row in _read_corpus_rows())
    _validate_corpus(items=items)
    return items

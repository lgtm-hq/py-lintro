#!/usr/bin/env python3
"""Generate ``lintro/ai/review/checklist/corpus.schema.json``.

The built-in review checklist corpus lives in YAML under
``lintro/ai/review/checklist/corpus/``. Its vocabulary (categories, file
domains, language tags) and its tier/id bounds are owned by Python:
:class:`~lintro.enums.review_category.ReviewCategory`,
:class:`~lintro.enums.file_domain.FileDomain`, ``identify.identify.ALL_TAGS``
and :mod:`lintro.ai.review.constants`. This script projects those sources into
a Draft 2020-12 JSON Schema so editors can offer completion and inline
validation while authoring the corpus.

The emitted schema is a **generated artifact** — never hand-edit it. The
committed copy is guarded by ``--check`` (and by
``tests/unit/ai/review/test_checklist_corpus_schema.py``), so editing an enum
without regenerating fails CI.

Modes:
    default: write the schema file, exit 0.
    --check: exit 1 with a unified diff if writing would change anything,
             exit 0 when the committed schema is in sync.

Structure and exit-code conventions mirror
``scripts/ci/generate-tool-versions.py``.
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]

# Make the ``lintro`` package importable when this script is executed directly
# from a checkout without an editable install.
sys.path.insert(0, str(REPO_ROOT))

# Imports must follow the sys.path bootstrap above.
from identify.identify import ALL_TAGS  # noqa: E402

from lintro.ai.review.constants import (  # noqa: E402
    TIER1_CHECKLIST_ID_END,
    TIER1_CHECKLIST_ID_START,
    TIER2_CHECKLIST_ID_START,
)
from lintro.enums.file_domain import FileDomain  # noqa: E402
from lintro.enums.review_category import ReviewCategory  # noqa: E402

EXIT_OK = 0
EXIT_DRIFT = 1

SCHEMA_PATH = (
    REPO_ROOT / "lintro" / "ai" / "review" / "checklist" / "corpus.schema.json"
)

GENERATOR_REL_PATH = "scripts/generate-checklist-corpus-schema.py"

SCHEMA_DESCRIPTION = (
    "GENERATED FILE - do not hand-edit. Produced by "
    f"{GENERATOR_REL_PATH} from the Python enums ReviewCategory and FileDomain, "
    "identify.identify.ALL_TAGS, and the tier/id bounds in "
    "lintro.ai.review.constants. Regenerate with "
    f"`uv run python {GENERATOR_REL_PATH}` after changing any of those sources. "
    "Cross-row invariants (unique ids, unique normalized question text) are not "
    "expressible here and stay in lintro.ai.review.checklist.loader."
)


def build_schema() -> dict[str, Any]:
    """Build the checklist corpus JSON Schema document.

    Enum members and language tags are emitted sorted so regenerated diffs stay
    reviewable.

    Returns:
        dict[str, Any]: Draft 2020-12 schema describing a corpus YAML file.
    """
    categories = sorted(member.value for member in ReviewCategory)
    domains = sorted(member.value for member in FileDomain)
    languages = sorted(ALL_TAGS)

    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": (
            "https://github.com/lgtm-hq/py-lintro/blob/main/lintro/ai/review/"
            "checklist/corpus.schema.json"
        ),
        "title": "Lintro built-in review checklist corpus",
        "description": SCHEMA_DESCRIPTION,
        "type": "array",
        "items": {"$ref": "#/$defs/checklistRow"},
        "$defs": {
            "checklistRow": {
                "type": "object",
                "title": "Checklist row",
                "description": (
                    "One built-in checklist item, parsed into a ChecklistItem "
                    "by lintro.ai.review.checklist.loader."
                ),
                "additionalProperties": False,
                "required": sorted(
                    ["id", "tier", "category", "question", "domains", "languages"],
                ),
                "properties": {
                    "category": {
                        "$ref": "#/$defs/reviewCategory",
                        "description": "Review finding category for this item.",
                    },
                    "domains": {
                        "type": "array",
                        "description": (
                            "File domains that trigger this item; empty for "
                            "Tier 1 (always-included) items."
                        ),
                        "items": {"$ref": "#/$defs/fileDomain"},
                        "uniqueItems": True,
                    },
                    "id": {
                        "type": "integer",
                        "description": (
                            f"Unique item id. Tier 1 uses "
                            f"{TIER1_CHECKLIST_ID_START}-{TIER1_CHECKLIST_ID_END}; "
                            f"Tier 2 uses >= {TIER2_CHECKLIST_ID_START}."
                        ),
                        "minimum": TIER1_CHECKLIST_ID_START,
                    },
                    "languages": {
                        "type": "array",
                        "description": (
                            "identify language tags that trigger this item; "
                            "empty for Tier 1 (always-included) items."
                        ),
                        "items": {"$ref": "#/$defs/languageTag"},
                        "uniqueItems": True,
                    },
                    "question": {
                        "type": "string",
                        "description": "Reviewer-facing question text.",
                        "minLength": 1,
                    },
                    "tier": {
                        "type": "integer",
                        "description": (
                            "1 for always-included items, 2 for "
                            "domain/language-triggered items."
                        ),
                        "enum": [1, 2],
                    },
                },
                "allOf": [
                    {
                        "if": {
                            "properties": {"tier": {"const": 1}},
                            "required": ["tier"],
                        },
                        "then": {
                            "properties": {
                                "domains": {"maxItems": 0},
                                "id": {
                                    "maximum": TIER1_CHECKLIST_ID_END,
                                    "minimum": TIER1_CHECKLIST_ID_START,
                                },
                                "languages": {"maxItems": 0},
                            },
                        },
                    },
                    {
                        "if": {
                            "properties": {"tier": {"const": 2}},
                            "required": ["tier"],
                        },
                        "then": {
                            "properties": {
                                "id": {"minimum": TIER2_CHECKLIST_ID_START},
                            },
                        },
                    },
                ],
            },
            "fileDomain": {
                "title": "FileDomain",
                "description": "Values of lintro.enums.file_domain.FileDomain.",
                "type": "string",
                "enum": domains,
            },
            "languageTag": {
                "title": "Language tag",
                "description": "Values of identify.identify.ALL_TAGS.",
                "type": "string",
                "enum": languages,
            },
            "reviewCategory": {
                "title": "ReviewCategory",
                "description": "Values of lintro.enums.review_category.ReviewCategory.",
                "type": "string",
                "enum": categories,
            },
        },
    }


def render_schema() -> str:
    """Render the schema document as the committed file text.

    Returns:
        str: JSON text with stable (sorted) key ordering and a trailing newline.
    """
    return json.dumps(build_schema(), indent=2, sort_keys=True) + "\n"


def diff_text(label: str, current: str, desired: str) -> str:
    """Return a unified diff between current and desired text, or empty.

    Args:
        label: File label used in the unified-diff header.
        current: Current file contents.
        desired: Desired file contents.

    Returns:
        str: Unified diff string, or empty when ``current == desired``.
    """
    if current == desired:
        return ""
    diff = difflib.unified_diff(
        current.splitlines(keepends=True),
        desired.splitlines(keepends=True),
        fromfile=f"a/{label}",
        tofile=f"b/{label}",
    )
    return "".join(diff)


def main(argv: list[str] | None = None) -> int:
    """Entry point.

    Args:
        argv: Optional argv override (for tests).

    Returns:
        int: Process exit code.
    """
    parser = argparse.ArgumentParser(
        description="Generate the review checklist corpus JSON Schema.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 with a diff if the schema would change; do not write.",
    )
    args = parser.parse_args(argv)

    desired = render_schema()
    current = SCHEMA_PATH.read_text(encoding="utf-8") if SCHEMA_PATH.exists() else ""

    if args.check:
        schema_diff = diff_text(
            str(SCHEMA_PATH.relative_to(REPO_ROOT)),
            current,
            desired,
        )
        if schema_diff:
            sys.stdout.write(schema_diff)
            print(
                f"\nDrift detected. Run {GENERATOR_REL_PATH} to regenerate.",
                file=sys.stderr,
            )
            return EXIT_DRIFT
        return EXIT_OK

    SCHEMA_PATH.write_text(desired, encoding="utf-8")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())

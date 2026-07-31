"""Drift guard for the generated checklist corpus JSON Schema.

The schema at ``lintro/ai/review/checklist/corpus.schema.json`` is generated
from the Python enums by ``scripts/generate-checklist-corpus-schema.py``. These
tests fail when the committed artifact drifts from the generator output, when
its enums stop matching the Python vocabulary, or when the corpus modelines
that associate it with editors are removed.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from assertpy import assert_that
from identify.identify import ALL_TAGS

from lintro.ai.review.checklist.loader import load_builtin_checklist
from lintro.ai.review.constants import (
    TIER1_CHECKLIST_ID_END,
    TIER1_CHECKLIST_ID_START,
    TIER2_CHECKLIST_ID_START,
)
from lintro.enums.file_domain import FileDomain
from lintro.enums.review_category import ReviewCategory

REPO_ROOT = Path(__file__).resolve().parents[4]
SCRIPT_PATH = REPO_ROOT / "scripts" / "generate-checklist-corpus-schema.py"
SCHEMA_PATH = (
    REPO_ROOT / "lintro" / "ai" / "review" / "checklist" / "corpus.schema.json"
)
CORPUS_DIR = REPO_ROOT / "lintro" / "ai" / "review" / "checklist" / "corpus"
CORPUS_FILES = ("tier1.yaml", "tier2.yaml")


@pytest.fixture(scope="module")
def generator() -> ModuleType:
    """Import the hyphen-named generator script as a module.

    Returns:
        ModuleType: Module exposing ``build_schema``, ``render_schema`` and
        ``main``.
    """
    spec = importlib.util.spec_from_file_location(
        "generate_checklist_corpus_schema",
        SCRIPT_PATH,
    )
    if spec is None or spec.loader is None:
        pytest.fail(f"could not load generator script at {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["generate_checklist_corpus_schema"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def committed_schema() -> dict[str, Any]:
    """Load the committed schema artifact.

    Returns:
        dict[str, Any]: Parsed schema document.
    """
    data: dict[str, Any] = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return data


def test_committed_schema_matches_generator_output(generator: ModuleType) -> None:
    """The committed schema is byte-identical to freshly generated output."""
    assert_that(SCHEMA_PATH.read_text(encoding="utf-8")).described_as(
        "corpus.schema.json is stale; run "
        "`uv run python scripts/generate-checklist-corpus-schema.py`",
    ).is_equal_to(generator.render_schema())


def test_generator_check_mode_reports_no_drift(generator: ModuleType) -> None:
    """``--check`` exits 0 when the committed schema is in sync."""
    assert_that(generator.main(["--check"])).is_equal_to(0)


def test_generator_check_mode_detects_drift(
    generator: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--check`` exits non-zero and diffs when the artifact is stale."""
    stale = tmp_path / "corpus.schema.json"
    stale.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(generator, "SCHEMA_PATH", stale)
    monkeypatch.setattr(generator, "REPO_ROOT", tmp_path)

    assert_that(generator.main(["--check"])).is_equal_to(1)


def test_generator_write_mode_rewrites_artifact(
    generator: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default (write) mode writes the rendered schema and exits 0."""
    target = tmp_path / "corpus.schema.json"
    monkeypatch.setattr(generator, "SCHEMA_PATH", target)

    assert_that(generator.main([])).is_equal_to(0)
    assert_that(target.read_text(encoding="utf-8")).is_equal_to(
        generator.render_schema(),
    )


def test_schema_declares_draft_2020_12(committed_schema: dict[str, Any]) -> None:
    """The schema pins the Draft 2020-12 dialect."""
    assert_that(committed_schema["$schema"]).is_equal_to(
        "https://json-schema.org/draft/2020-12/schema",
    )


def test_schema_description_marks_it_generated(
    committed_schema: dict[str, Any],
) -> None:
    """The schema description warns against hand-editing."""
    assert_that(committed_schema["description"]).contains(
        "GENERATED FILE - do not hand-edit",
        "scripts/generate-checklist-corpus-schema.py",
    )


def test_schema_category_enum_matches_python_enum(
    committed_schema: dict[str, Any],
) -> None:
    """The category enum equals ``ReviewCategory`` members."""
    assert_that(set(committed_schema["$defs"]["reviewCategory"]["enum"])).is_equal_to(
        {member.value for member in ReviewCategory},
    )


def test_schema_domain_enum_matches_python_enum(
    committed_schema: dict[str, Any],
) -> None:
    """The domain enum equals ``FileDomain`` members."""
    assert_that(set(committed_schema["$defs"]["fileDomain"]["enum"])).is_equal_to(
        {member.value for member in FileDomain},
    )


def test_schema_language_enum_matches_identify_tags(
    committed_schema: dict[str, Any],
) -> None:
    """The language enum equals ``identify.ALL_TAGS``."""
    assert_that(set(committed_schema["$defs"]["languageTag"]["enum"])).is_equal_to(
        set(ALL_TAGS),
    )


@pytest.mark.parametrize(
    "def_name",
    ["fileDomain", "languageTag", "reviewCategory"],
    ids=["def=fileDomain", "def=languageTag", "def=reviewCategory"],
)
def test_schema_enums_are_sorted(
    committed_schema: dict[str, Any],
    def_name: str,
) -> None:
    """Enum values are emitted sorted so regeneration diffs stay reviewable.

    Args:
        committed_schema: Parsed schema document.
        def_name: ``$defs`` key holding the enum under test.
    """
    values = committed_schema["$defs"][def_name]["enum"]
    assert_that(values).is_equal_to(sorted(values))


def test_schema_row_requires_all_loader_fields(
    committed_schema: dict[str, Any],
) -> None:
    """Required row fields match the loader's required-field set."""
    row = committed_schema["$defs"]["checklistRow"]
    assert_that(set(row["required"])).is_equal_to(
        {"id", "tier", "category", "question", "domains", "languages"},
    )
    assert_that(row["additionalProperties"]).is_false()


def test_schema_constrains_tier_one_ids_and_empty_axes(
    committed_schema: dict[str, Any],
) -> None:
    """Tier 1 rows are bounded to the tier-1 id range with empty axes."""
    branches = committed_schema["$defs"]["checklistRow"]["allOf"]
    tier1 = next(
        branch
        for branch in branches
        if branch["if"]["properties"]["tier"]["const"] == 1
    )
    properties = tier1["then"]["properties"]
    assert_that(properties["id"]["minimum"]).is_equal_to(TIER1_CHECKLIST_ID_START)
    assert_that(properties["id"]["maximum"]).is_equal_to(TIER1_CHECKLIST_ID_END)
    assert_that(properties["domains"]["maxItems"]).is_equal_to(0)
    assert_that(properties["languages"]["maxItems"]).is_equal_to(0)


def test_schema_constrains_tier_two_id_floor(
    committed_schema: dict[str, Any],
) -> None:
    """Tier 2 rows are bounded below by the tier-2 id start."""
    branches = committed_schema["$defs"]["checklistRow"]["allOf"]
    tier2 = next(
        branch
        for branch in branches
        if branch["if"]["properties"]["tier"]["const"] == 2
    )
    assert_that(tier2["then"]["properties"]["id"]["minimum"]).is_equal_to(
        TIER2_CHECKLIST_ID_START,
    )


def test_committed_corpus_satisfies_schema_vocabulary(
    committed_schema: dict[str, Any],
) -> None:
    """Every built-in corpus row uses values the schema accepts.

    Structural check only (no ``jsonschema`` dependency): field set, enum
    membership, and the tier/id constraints the schema encodes.

    Args:
        committed_schema: Parsed schema document.
    """
    defs = committed_schema["$defs"]
    row_schema = defs["checklistRow"]
    categories = set(defs["reviewCategory"]["enum"])
    domains = set(defs["fileDomain"]["enum"])
    languages = set(defs["languageTag"]["enum"])

    for item in load_builtin_checklist():
        assert_that(set(row_schema["properties"])).is_equal_to(
            set(row_schema["required"]),
        )
        assert_that(categories).contains(item.category.value)
        assert_that({domain.value for domain in item.domains}).is_subset_of(domains)
        assert_that(set(item.languages)).is_subset_of(languages)
        assert_that(item.tier).is_in(*row_schema["properties"]["tier"]["enum"])
        if item.tier == 1:
            assert_that(item.id).is_between(
                TIER1_CHECKLIST_ID_START,
                TIER1_CHECKLIST_ID_END,
            )
            assert_that(item.domains).is_empty()
            assert_that(item.languages).is_empty()
        else:
            assert_that(item.id).is_greater_than_or_equal_to(TIER2_CHECKLIST_ID_START)


@pytest.mark.parametrize(
    "file_name",
    CORPUS_FILES,
    ids=["corpus=tier1", "corpus=tier2"],
)
@pytest.mark.parametrize(
    "modeline",
    [
        "# yaml-language-server: $schema=../corpus.schema.json",
        "# $schema: ../corpus.schema.json",
    ],
    ids=["modeline=vscode", "modeline=jetbrains"],
)
def test_corpus_files_carry_dual_schema_modelines(
    file_name: str,
    modeline: str,
) -> None:
    """Both corpus files declare both editor schema modelines.

    Args:
        file_name: Corpus YAML file name.
        modeline: Expected modeline substring.
    """
    text = (CORPUS_DIR / file_name).read_text(encoding="utf-8")
    assert_that(text).contains(modeline)


@pytest.mark.parametrize(
    "file_name",
    CORPUS_FILES,
    ids=["corpus=tier1", "corpus=tier2"],
)
def test_corpus_files_document_regeneration(file_name: str) -> None:
    """Both corpus headers point authors at the generator command.

    Args:
        file_name: Corpus YAML file name.
    """
    text = (CORPUS_DIR / file_name).read_text(encoding="utf-8")
    assert_that(text).contains(
        "uv run python scripts/generate-checklist-corpus-schema.py",
    )

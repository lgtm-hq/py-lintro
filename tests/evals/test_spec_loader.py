"""Tests for the matrix and corpus file loaders."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that
from review_matrix.spec_loader import (
    SpecError,
    load_corpus,
    load_matrix,
    parse_corpus,
    parse_matrix,
)

from lintro.ai.config_overrides import (
    ENV_MAX_COST_USD,
    ENV_MODEL,
    ENV_PROVIDER,
    ENV_TRANSPORT,
)
from lintro.ai.review.models.review_finding import Severity

HARNESS_ROOT = Path(__file__).resolve().parents[2] / "evals" / "review-efficacy"

MINIMAL_MATRIX: dict[str, Any] = {
    "version": 1,
    "repeats": 3,
    "configs": [
        {
            "id": "anthropic-opus-api",
            "provider": "anthropic",
            "model": "claude-opus-4-5",
            "transport": "api",
            "max_cost_usd": 3.0,
            "projected_cost_usd": 1.2,
        },
    ],
}

MINIMAL_CORPUS: dict[str, Any] = {
    "version": 1,
    "repo": "lgtm-hq/py-lintro",
    "items": [{"id": "pr-1", "pr": 1}],
}


def test_parse_matrix_reads_configs_in_file_order() -> None:
    """Configs keep the order the matrix file declares them in."""
    document = {
        **MINIMAL_MATRIX,
        "configs": [
            MINIMAL_MATRIX["configs"][0],
            {
                "id": "cursor-cli",
                "provider": "cursor",
                "model": "grok-4.6",
                "transport": "cli",
                "max_cost_usd": 2.0,
            },
        ],
    }

    spec = parse_matrix(document)

    assert_that([config.config_id for config in spec.configs]).is_equal_to(
        ["anthropic-opus-api", "cursor-cli"],
    )


def test_parse_matrix_defaults_projected_cost_to_the_ceiling() -> None:
    """An omitted projection falls back to the cap, never to something lower."""
    document = {
        **MINIMAL_MATRIX,
        "configs": [
            {
                "id": "cursor-cli",
                "provider": "cursor",
                "model": "grok-4.6",
                "transport": "cli",
                "max_cost_usd": 2.0,
            },
        ],
    }

    spec = parse_matrix(document)

    assert_that(spec.configs[0].projected_cost_usd).is_equal_to(2.0)


def test_matrix_config_env_overrides_use_the_documented_variables() -> None:
    """A config is expressed purely as ``LINTRO_AI_*`` env overrides."""
    spec = parse_matrix(MINIMAL_MATRIX)

    overrides = spec.configs[0].env_overrides

    assert_that(overrides).is_equal_to(
        {
            ENV_PROVIDER: "anthropic",
            ENV_MODEL: "claude-opus-4-5",
            ENV_TRANSPORT: "api",
            ENV_MAX_COST_USD: "3",
        },
    )


def test_parse_matrix_rejects_an_empty_config_list() -> None:
    """A matrix with no configs measures nothing and is refused."""
    with pytest.raises(SpecError):
        parse_matrix({"version": 1, "configs": []})


def test_parse_matrix_rejects_duplicate_config_ids() -> None:
    """Duplicate ids would silently overwrite a config's run directory."""
    document = {
        **MINIMAL_MATRIX,
        "configs": [MINIMAL_MATRIX["configs"][0], MINIMAL_MATRIX["configs"][0]],
    }

    with pytest.raises(SpecError):
        parse_matrix(document)


def test_parse_matrix_rejects_a_non_positive_cost_cap() -> None:
    """A zero or negative cap is ambiguous, so it is refused up front."""
    document = {
        **MINIMAL_MATRIX,
        "configs": [{**MINIMAL_MATRIX["configs"][0], "max_cost_usd": 0}],
    }

    with pytest.raises(SpecError):
        parse_matrix(document)


def test_parse_corpus_applies_the_corpus_level_repo() -> None:
    """Items inherit the corpus-level repository when they omit their own."""
    corpus = parse_corpus(MINIMAL_CORPUS)

    assert_that(corpus.items[0].repo).is_equal_to("lgtm-hq/py-lintro")


def test_parse_corpus_reads_labels_and_marks_the_item_labeled() -> None:
    """An item with expected findings joins the efficacy table."""
    document = {
        **MINIMAL_CORPUS,
        "items": [
            {
                "id": "pr-1",
                "pr": 1,
                "expected_findings": [
                    {
                        "file": "lintro/example.py",
                        "category": "correctness",
                        "title": "Off by one",
                        "severity": "P1",
                    },
                ],
            },
        ],
    }

    corpus = parse_corpus(document)

    assert_that(corpus.labeled_items).is_length(1)
    label = corpus.items[0].labeled_findings[0]
    assert_that(label.severity).is_equal_to(Severity.P1)
    assert_that(label.to_finding().title).is_equal_to("Off by one")


def test_parse_corpus_defaults_a_label_severity_to_p2() -> None:
    """An unlabeled severity is a P2, not the verdict-moving P1."""
    document = {
        **MINIMAL_CORPUS,
        "items": [
            {
                "id": "pr-1",
                "pr": 1,
                "expected_findings": [
                    {
                        "file": "lintro/example.py",
                        "category": "correctness",
                        "title": "Off by one",
                    },
                ],
            },
        ],
    }

    corpus = parse_corpus(document)

    assert_that(corpus.items[0].labeled_findings[0].severity).is_equal_to(
        Severity.P2,
    )


def test_parse_corpus_rejects_an_unknown_severity() -> None:
    """A typo'd severity fails loudly instead of becoming a default."""
    document = {
        **MINIMAL_CORPUS,
        "items": [
            {
                "id": "pr-1",
                "pr": 1,
                "expected_findings": [
                    {
                        "file": "lintro/example.py",
                        "category": "correctness",
                        "title": "Off by one",
                        "severity": "P9",
                    },
                ],
            },
        ],
    }

    with pytest.raises(SpecError):
        parse_corpus(document)


def test_parse_corpus_rejects_an_item_without_a_repo() -> None:
    """An item with no repo and no corpus default cannot be reviewed."""
    with pytest.raises(SpecError):
        parse_corpus({"version": 1, "items": [{"id": "pr-1", "pr": 1}]})


def test_load_matrix_reads_json_as_well_as_yaml(tmp_path: Path) -> None:
    """JSON is valid YAML, so a matrix may be committed in either form."""
    path = tmp_path / "matrix.json"
    path.write_text(json.dumps(MINIMAL_MATRIX), encoding="utf-8")

    spec = load_matrix(path)

    assert_that(spec.configs).is_length(1)


def test_load_matrix_reports_a_missing_file(tmp_path: Path) -> None:
    """A missing matrix file is a spec error, not a traceback."""
    with pytest.raises(SpecError):
        load_matrix(tmp_path / "absent.yaml")


def test_committed_matrix_file_parses() -> None:
    """The matrix committed under evals/ stays loadable."""
    spec = load_matrix(HARNESS_ROOT / "matrix.yaml")

    assert_that(spec.repeats).is_greater_than_or_equal_to(3)
    assert_that(spec.configs).is_not_empty()


def test_committed_corpus_file_parses() -> None:
    """The corpus committed under evals/ stays loadable."""
    corpus = load_corpus(HARNESS_ROOT / "corpus" / "corpus.yaml")

    assert_that(corpus.items).is_not_empty()

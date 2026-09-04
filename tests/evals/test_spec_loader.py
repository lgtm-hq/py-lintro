"""Tests for the matrix and corpus file loaders."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that
from review_matrix.spec_loader import (
    DEFAULT_DEPTH,
    DEFAULT_REPEATS,
    DEFAULT_TIMEOUT_SECONDS,
    SpecError,
    load_corpus,
    load_matrix,
    parse_corpus,
    parse_matrix,
)

from lintro.ai.config_overrides import (
    ENV_ENABLED,
    ENV_MAX_COST_USD,
    ENV_MODEL,
    ENV_PROVIDER,
    ENV_REVIEW,
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
            ENV_ENABLED: "1",
            ENV_REVIEW: "1",
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


def test_parse_corpus_requires_a_label_severity() -> None:
    """An omitted severity is an authoring error, not a silent P2.

    Severity is what the expected verdict is derived from, so a forgotten P1
    must fail loudly rather than be stored as a P2.
    """
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

    with pytest.raises(SpecError, match="'severity' is required"):
        parse_corpus(document)


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


@pytest.mark.parametrize(
    "config_id",
    ["../escape", "nested/id", "back\\slash", "/absolute", "..", ".hidden", ""],
)
def test_parse_matrix_rejects_an_unsafe_config_id(config_id: str) -> None:
    """Ids become output path segments, so traversal and separators are refused.

    Args:
        config_id: Candidate id that must not be accepted.
    """
    document = json.loads(json.dumps(MINIMAL_MATRIX))
    document["configs"][0]["id"] = config_id

    with pytest.raises(SpecError):
        parse_matrix(document)


@pytest.mark.parametrize(
    "item_id",
    ["../escape", "nested/id", "back\\slash", "/absolute", "..", ".hidden"],
)
def test_parse_corpus_rejects_an_unsafe_item_id(item_id: str) -> None:
    """A corpus item id cannot redirect where its payloads are written.

    Args:
        item_id: Candidate id that must not be accepted.
    """
    document = json.loads(json.dumps(MINIMAL_CORPUS))
    document["items"][0]["id"] = item_id

    with pytest.raises(SpecError):
        parse_corpus(document)


@pytest.mark.parametrize("item_id", ["pr-1", "pr_1.2", "PR1"])
def test_parse_corpus_accepts_a_safe_item_id(item_id: str) -> None:
    """Ordinary ids keep working.

    Args:
        item_id: Candidate id that must be accepted.
    """
    document = json.loads(json.dumps(MINIMAL_CORPUS))
    document["items"][0]["id"] = item_id

    corpus = parse_corpus(document)

    assert_that(corpus.items[0].item_id).is_equal_to(item_id)


@pytest.mark.parametrize("repeats", [True, False, 2.5, float("nan"), float("inf")])
def test_parse_matrix_rejects_a_non_integral_repeat_count(repeats: object) -> None:
    """Booleans, fractional floats and non-finite values are not repeat counts.

    Args:
        repeats: Candidate value that must not be accepted.
    """
    document = json.loads(json.dumps(MINIMAL_MATRIX))
    document["repeats"] = repeats

    with pytest.raises(SpecError, match="must be an integer"):
        parse_matrix(document)


@pytest.mark.parametrize("cost", [True, float("nan"), float("inf"), float("-inf")])
def test_parse_matrix_rejects_a_non_finite_cost_cap(cost: object) -> None:
    """A cost cap must be a real, finite, positive number.

    Args:
        cost: Candidate value that must not be accepted.
    """
    document = json.loads(json.dumps(MINIMAL_MATRIX))
    document["configs"][0]["max_cost_usd"] = cost

    with pytest.raises(SpecError, match="must be a (number|positive)"):
        parse_matrix(document)


def test_parse_matrix_rejects_a_list_valued_provider() -> None:
    """A YAML list must not be stringified into a provider name."""
    document = json.loads(json.dumps(MINIMAL_MATRIX))
    document["configs"][0]["provider"] = ["anthropic", "openai"]

    with pytest.raises(SpecError, match="'provider' must be a string"):
        parse_matrix(document)


def test_parse_matrix_rejects_a_dict_valued_model() -> None:
    """A YAML mapping must not be stringified into a model name."""
    document = json.loads(json.dumps(MINIMAL_MATRIX))
    document["configs"][0]["model"] = {"name": "claude-sonnet-4-6"}

    with pytest.raises(SpecError, match="'model' must be a string"):
        parse_matrix(document)


def test_parse_corpus_rejects_a_non_string_repo() -> None:
    """A corpus item's repo must be a string, not a coerced structure."""
    document = json.loads(json.dumps(MINIMAL_CORPUS))
    document["items"][0]["repo"] = ["lgtm-hq", "py-lintro"]

    with pytest.raises(SpecError, match="'repo' must be a string"):
        parse_corpus(document)


def test_parse_corpus_rejects_a_non_string_title() -> None:
    """A corpus item's title must be a string."""
    document = json.loads(json.dumps(MINIMAL_CORPUS))
    document["items"][0]["title"] = {"text": "a pull request"}

    with pytest.raises(SpecError, match="'title' must be a string"):
        parse_corpus(document)


def test_load_matrix_reports_an_unreadable_path_as_a_spec_error(
    tmp_path: Path,
) -> None:
    """A directory in place of a spec file is a SpecError, not an OSError.

    Args:
        tmp_path: Pytest temporary directory.
    """
    directory = tmp_path / "matrix.yaml"
    directory.mkdir()

    with pytest.raises(SpecError, match="cannot read"):
        load_matrix(directory)


@pytest.mark.parametrize("depth", [4, 10])
def test_parse_matrix_rejects_a_depth_the_review_cli_would_refuse(
    depth: int,
) -> None:
    """``lintro review --depth`` is an IntRange(1, 3); a matrix cannot exceed it.

    Args:
        depth: Out-of-range depth that must not parse.
    """
    document = json.loads(json.dumps(MINIMAL_MATRIX))
    document["depth"] = depth

    with pytest.raises(SpecError, match="'depth' must be between 1 and 3"):
        parse_matrix(document)


def test_parse_matrix_applies_the_documented_defaults() -> None:
    """Omitted repeats, depth and timeout fall back to the DEFAULT_* values."""
    document = json.loads(json.dumps(MINIMAL_MATRIX))
    document.pop("repeats")

    spec = parse_matrix(document)

    assert_that(spec.repeats).is_equal_to(DEFAULT_REPEATS)
    assert_that(spec.depth).is_equal_to(DEFAULT_DEPTH)
    assert_that(spec.timeout_seconds).is_equal_to(DEFAULT_TIMEOUT_SECONDS)


def test_parse_corpus_prefers_an_item_repo_over_the_corpus_default() -> None:
    """A per-item repo overrides the corpus-level default."""
    document = json.loads(json.dumps(MINIMAL_CORPUS))
    document["items"][0]["repo"] = "lgtm-hq/other"

    corpus = parse_corpus(document)

    assert_that(corpus.items[0].repo).is_equal_to("lgtm-hq/other")


def test_parse_corpus_rejects_duplicate_item_ids() -> None:
    """Two items sharing an id would write into the same run directory."""
    document = json.loads(json.dumps(MINIMAL_CORPUS))
    document["items"].append({"id": "pr-1", "pr": 2})

    with pytest.raises(SpecError, match="duplicate item id"):
        parse_corpus(document)


def test_load_corpus_reports_a_missing_file(tmp_path: Path) -> None:
    """A missing corpus path is a SpecError, like a missing matrix path.

    Args:
        tmp_path: Pytest temporary directory.
    """
    with pytest.raises(SpecError, match="cannot read"):
        load_corpus(tmp_path / "nope.yaml")


def test_load_matrix_reports_malformed_yaml(tmp_path: Path) -> None:
    """Unparseable YAML is a SpecError rather than a YAMLError traceback.

    Args:
        tmp_path: Pytest temporary directory.
    """
    bad = tmp_path / "matrix.yaml"
    bad.write_text("configs: [\n  - id: 'unterminated\n", encoding="utf-8")

    with pytest.raises(SpecError, match="cannot parse"):
        load_matrix(bad)

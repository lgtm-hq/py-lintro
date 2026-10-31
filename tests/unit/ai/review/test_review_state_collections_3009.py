"""Malformed artifact collections cannot provide review coverage (#3009)."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from assertpy import assert_that
from loguru import logger

from lintro.ai.review.already_reviewed import evaluate_already_reviewed
from lintro.ai.review.github_constants import STATE_VERSION
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.coverage_record import CoverageRecord
from lintro.ai.review.models.finding_record import FindingRecord
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.run_identity import RunIdentity
from lintro.ai.review.models.run_record import RunRecord
from lintro.ai.review.patch_hash import normalized_patch_hash
from lintro.ai.review.state_store import load_ci_state, load_local_state

_COLLECTIONS = (
    "runs",
    "findings",
    "coverage",
    "flagged_files",
    "pending_invalidations",
    "consumed_flags",
)


@pytest.fixture
def covered_review() -> tuple[ReviewContext, ReviewState]:
    """Provide a finished round with an open finding at an unchanged head."""
    diff = (
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n" "@@ -1 +1 @@\n-old\n+new\n"
    )
    context = ReviewContext(
        base_ref="main",
        head_ref="abc123",
        changed_files=[ChangedFile("a.py", "modified", 1, 1)],
        unified_diff=diff,
        pr_metadata=None,
    )
    state = ReviewState(
        repo="lgtm-hq/py-lintro",
        pr_number=3009,
        runs=(RunRecord(identity=RunIdentity(round=1, sha=context.head_ref)),),
        findings=(FindingRecord(fingerprint="open-finding", file="a.py"),),
        coverage=(CoverageRecord("a.py", normalized_patch_hash(diff), round=1),),
    )
    return context, state


@pytest.mark.parametrize("field", _COLLECTIONS)
@pytest.mark.parametrize("malformed", [None, {}, "not-a-list", False, 0])
def test_decoder_rejects_non_list_collections(field: str, malformed: object) -> None:
    """A malformed container is unknown history, not an empty collection."""
    payload = ReviewState().to_artifact_dict()
    payload[field] = malformed

    with pytest.raises(ValueError, match=field):
        ReviewState.from_artifact_dict(payload)


@pytest.mark.parametrize(
    ("version", "field"),
    [
        (version, field)
        for version in range(2, STATE_VERSION + 1)
        for field in _COLLECTIONS
        if version >= 3 or field in {"runs", "findings"}
    ],
)
def test_decoder_rejects_missing_required_collections(version: int, field: str) -> None:
    """Every supported schema requires the collections its writer emitted."""
    payload = ReviewState().to_artifact_dict()
    payload["schema_version"] = payload["version"] = version
    del payload[field]

    with pytest.raises(ValueError, match=field):
        ReviewState.from_artifact_dict(payload)


@pytest.mark.parametrize("field", ["findings", "pending_invalidations"])
@pytest.mark.parametrize("backend", ["ci", "local"])
def test_null_collections_discard_coverage_and_require_review(
    tmp_path: Path,
    covered_review: tuple[ReviewContext, ReviewState],
    field: str,
    backend: str,
) -> None:
    """Null findings or owed re-reads cannot turn into AlreadyReviewed."""
    context, state = covered_review
    if field == "pending_invalidations":
        state = replace(
            state,
            findings=(),
            pending_invalidations=(("a.py", "group_invalidated"),),
        )
    payload = state.to_artifact_dict()
    payload[field] = None
    path = tmp_path / "pr-3009.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    logs: list[str] = []
    logger.add(logs.append, format="{message}")

    if backend == "ci":
        loaded = load_ci_state(directory=tmp_path, repo=state.repo, pr_number=3009)
    else:
        loaded = load_local_state(
            key="pr-3009",
            directory=tmp_path,
            repo=state.repo,
            pr_number=3009,
        )

    assert_that(
        evaluate_already_reviewed(
            context=context,
            prior=loaded,
            force_full=False,
            reusable_rounds=None,
            env={},
        ),
    ).is_none()
    assert_that(loaded).is_equal_to(ReviewState())
    assert_that("".join(logs)).contains(path.name, field)


@pytest.mark.parametrize("version", range(2, STATE_VERSION + 1))
def test_empty_lists_preserve_proven_coverage(
    tmp_path: Path,
    covered_review: tuple[ReviewContext, ReviewState],
    version: int,
) -> None:
    """Measured empty findings and invalidations still allow a covered skip."""
    context, state = covered_review
    payload = replace(state, findings=()).to_artifact_dict()
    payload["schema_version"] = payload["version"] = version
    (tmp_path / "state.json").write_text(json.dumps(payload), encoding="utf-8")

    loaded = load_ci_state(directory=tmp_path, repo=state.repo, pr_number=3009)
    decision = evaluate_already_reviewed(
        context=context,
        prior=loaded,
        force_full=False,
        reusable_rounds=None,
        env={},
    )

    assert_that(loaded.runs).is_length(1)
    assert_that(loaded.runs[0].identity.sha).is_equal_to(context.head_ref)
    assert_that(loaded.runs[0].identity.round).is_equal_to(1)
    assert_that(loaded.coverage).is_equal_to(state.coverage)
    assert_that(decision).is_not_none()
    assert decision is not None
    assert_that(decision.open_findings).is_equal_to(0)
    assert_that(decision.files_covered).is_equal_to(1)


def test_v2_history_does_not_require_later_artifact_collections() -> None:
    """The v2 runs/findings shape remains readable without v3 resume fields."""
    loaded = ReviewState.from_artifact_dict(
        {"version": 2, "runs": [{"round": 2}], "findings": []},
    )

    assert_that(loaded.next_round).is_equal_to(3)
    assert_that(loaded.coverage).is_empty()
    assert_that(loaded.pending_invalidations).is_empty()


def test_invalid_later_part_cannot_clear_an_owed_review(
    tmp_path: Path,
    covered_review: tuple[ReviewContext, ReviewState],
) -> None:
    """Union only trusts valid parts, including their pending invalidations."""
    context, state = covered_review
    state = replace(
        state,
        findings=(),
        pending_invalidations=(("a.py", "import_invalidated"),),
    )
    (tmp_path / "part-0001.json").write_text(
        json.dumps(state.to_artifact_dict()),
        encoding="utf-8",
    )
    payload = replace(state, coverage=(CoverageRecord("untrusted.py", "hash"),))
    malformed = payload.to_artifact_dict()
    malformed["pending_invalidations"] = None
    (tmp_path / "part-0002.json").write_text(json.dumps(malformed), encoding="utf-8")

    loaded = load_ci_state(directory=tmp_path, repo=state.repo, pr_number=3009)

    assert_that(loaded.coverage).is_equal_to(state.coverage)
    assert_that(loaded.pending_invalidations).is_equal_to(state.pending_invalidations)
    assert_that(loaded.open_findings).is_equal_to(state.open_findings)
    assert_that(
        evaluate_already_reviewed(
            context=context,
            prior=loaded,
            force_full=False,
            reusable_rounds=None,
            env={},
        ),
    ).is_none()

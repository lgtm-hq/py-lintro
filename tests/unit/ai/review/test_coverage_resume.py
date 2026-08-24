"""File-level coverage, queue order, and artifact state (#2154)."""

from __future__ import annotations

from pathlib import Path

from assertpy import assert_that

from lintro.ai.enums.config_source import ConfigSource
from lintro.ai.enums.cost_basis import CostBasis
from lintro.ai.review.cost_cap import cap_is_enforced
from lintro.ai.review.coverage import (
    ClassifiedFile,
    classify_files,
    coverage_counts,
    hashes_for_diffs,
    queue_paths,
    review_eligible_paths,
)
from lintro.ai.review.enums.changed_file_status import ChangedFileStatus
from lintro.ai.review.enums.file_review_need import FileReviewNeed
from lintro.ai.review.enums.file_skip_reason import FileSkipReason
from lintro.ai.review.enums.review_verdict import ReviewVerdict
from lintro.ai.review.import_graph import importers_of, parse_python_imports
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.coverage_record import CoverageRecord
from lintro.ai.review.models.flagged_file import FlaggedFile
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.skipped_file import SkippedFile
from lintro.ai.review.patch_hash import normalized_patch_hash
from lintro.ai.review.state_store import (
    load_ci_state,
    load_local_state,
    local_ledger_key,
    migrate_legacy_sticky,
    union_states,
    write_local_state,
    write_state_part,
)
from lintro.ai.review.verdict import apply_coverage_gate, derive_readiness_verdict


def test_patch_hash_ignores_context_and_headers() -> None:
    """Rebase context and hunk headers do not change the coverage key."""
    left = "@@ -1,3 +1,3 @@\n context\n-old\n+new\n"
    right = "@@ -10,5 +10,5 @@\n other context\n-old\n+new\n"
    assert_that(normalized_patch_hash(left)).is_equal_to(normalized_patch_hash(right))


def test_patch_hash_changes_when_added_line_changes() -> None:
    """An added-line edit invalidates coverage."""
    left = "+alpha\n-old\n"
    right = "+beta\n-old\n"
    assert_that(normalized_patch_hash(left)).is_not_equal_to(
        normalized_patch_hash(right),
    )


def test_never_reviewed_sorts_before_invalidated() -> None:
    """Capped rounds spend budget on never-reviewed files first."""
    classified = (
        ClassifiedFile("z.py", "h", FileReviewNeed.GROUP_INVALIDATED),
        ClassifiedFile("a.py", "h", FileReviewNeed.NEVER_REVIEWED),
        ClassifiedFile("m.py", "h", FileReviewNeed.DIRECTLY_CHANGED),
        ClassifiedFile("b.py", "h", FileReviewNeed.COVERED),
        ClassifiedFile("f.py", "h", FileReviewNeed.MODEL_FLAGGED),
    )
    assert_that(queue_paths(classified=classified)).is_equal_to(
        ("a.py", "m.py", "f.py", "z.py"),
    )


def test_identical_hash_inherits_sampled_coverage() -> None:
    """A sampled-out sibling with the same hash counts as covered."""
    hashes = {"keep.py": "aaa", "skip.py": "aaa", "other.py": "bbb"}
    classified = classify_files(
        eligible_paths=("keep.py", "skip.py", "other.py"),
        current_hashes=hashes,
        coverage=(CoverageRecord("keep.py", "aaa"),),
    )
    by_path = {item.path: item.need for item in classified}
    assert_that(by_path["keep.py"]).is_equal_to(FileReviewNeed.COVERED)
    assert_that(by_path["skip.py"]).is_equal_to(FileReviewNeed.COVERED)
    assert_that(by_path["other.py"]).is_equal_to(FileReviewNeed.NEVER_REVIEWED)


def test_group_invalidation_skips_broadcast() -> None:
    """pyproject.toml does not fan out to the rest of a group."""
    classified = classify_files(
        eligible_paths=("pkg/a.py", "pyproject.toml"),
        current_hashes={"pkg/a.py": "1", "pyproject.toml": "2"},
        coverage=(
            CoverageRecord("pkg/a.py", "1"),
            CoverageRecord("pyproject.toml", "old"),
        ),
        groups=(("pkg/a.py", "pyproject.toml"),),
    )
    by_path = {item.path: item.need for item in classified}
    assert_that(by_path["pyproject.toml"]).is_equal_to(FileReviewNeed.DIRECTLY_CHANGED)
    assert_that(by_path["pkg/a.py"]).is_equal_to(FileReviewNeed.COVERED)


def test_group_mate_is_invalidated_when_peer_changes() -> None:
    """A semantic-group mate of a changed file re-enters the queue."""
    classified = classify_files(
        eligible_paths=("a.py", "a_test.py"),
        current_hashes={"a.py": "new", "a_test.py": "same"},
        coverage=(
            CoverageRecord("a.py", "old"),
            CoverageRecord("a_test.py", "same"),
        ),
        groups=(("a.py", "a_test.py"),),
    )
    by_path = {item.path: item.need for item in classified}
    assert_that(by_path["a.py"]).is_equal_to(FileReviewNeed.DIRECTLY_CHANGED)
    assert_that(by_path["a_test.py"]).is_equal_to(FileReviewNeed.GROUP_INVALIDATED)


def test_import_invalidation_is_one_hop() -> None:
    """A→B→C with only C changed re-enters B, not A."""
    contents = {
        "a.py": "from b import x\n",
        "b.py": "from c import y\n",
        "c.py": "y = 1\n",
    }
    reverse = importers_of(
        changed_paths={"a.py", "b.py", "c.py"},
        contents=contents,
        directly_changed={"c.py"},
    )
    assert_that(reverse["c.py"]).is_equal_to({"b.py"})
    classified = classify_files(
        eligible_paths=("a.py", "b.py", "c.py"),
        current_hashes={"a.py": "1", "b.py": "1", "c.py": "2"},
        coverage=(
            CoverageRecord("a.py", "1"),
            CoverageRecord("b.py", "1"),
            CoverageRecord("c.py", "1"),
        ),
        import_importers=reverse,
    )
    by_path = {item.path: item.need for item in classified}
    assert_that(by_path["c.py"]).is_equal_to(FileReviewNeed.DIRECTLY_CHANGED)
    assert_that(by_path["b.py"]).is_equal_to(FileReviewNeed.IMPORT_INVALIDATED)
    assert_that(by_path["a.py"]).is_equal_to(FileReviewNeed.COVERED)


def test_flag_is_one_way_and_allowlisted() -> None:
    """Flags cannot invent files or push never-reviewed paths."""
    classified = classify_files(
        eligible_paths=("covered.py", "fresh.py"),
        current_hashes={"covered.py": "h1", "fresh.py": "h2"},
        coverage=(CoverageRecord("covered.py", "h1"),),
        flags=(
            FlaggedFile("covered.py", "contract change", "h1"),
            FlaggedFile("fresh.py", "please", "h2"),
            FlaggedFile("outside.py", "nope", "h3"),
        ),
    )
    by_path = {item.path: item.need for item in classified}
    assert_that(by_path["covered.py"]).is_equal_to(FileReviewNeed.MODEL_FLAGGED)
    assert_that(by_path["fresh.py"]).is_equal_to(FileReviewNeed.NEVER_REVIEWED)


def test_deleted_and_excluded_files_leave_the_denominator() -> None:
    """Deletions and path/config skips cannot block 100% coverage."""
    files = (
        ChangedFile("keep.py", ChangedFileStatus.MODIFIED, 1, 1),
        ChangedFile("gone.py", ChangedFileStatus.DELETED, 0, 3),
        ChangedFile("docs/x.md", ChangedFileStatus.MODIFIED, 1, 0),
    )
    skipped = (SkippedFile(path="docs/x.md", reason=FileSkipReason.PATH_FILTER),)
    assert_that(
        review_eligible_paths(changed_files=files, skipped=skipped),
    ).is_equal_to(("keep.py",))


def test_incomplete_overrides_ready() -> None:
    """Partial coverage can never present as ready."""
    findings_verdict = derive_readiness_verdict(findings=())
    assert_that(findings_verdict).is_equal_to(ReviewVerdict.READY)
    assert_that(
        apply_coverage_gate(
            findings_verdict=findings_verdict,
            coverage_complete=False,
        ),
    ).is_equal_to(ReviewVerdict.INCOMPLETE)


def test_yaml_cap_is_advisory_on_unpriceable() -> None:
    """Committed YAML does not hard-stop a subscription run."""
    assert_that(
        cap_is_enforced(source=ConfigSource.CONFIG, basis=CostBasis.UNPRICEABLE),
    ).is_false()
    assert_that(
        cap_is_enforced(source=ConfigSource.CONFIG, basis=CostBasis.BILLED),
    ).is_true()
    assert_that(
        cap_is_enforced(source=ConfigSource.ENV, basis=CostBasis.UNPRICEABLE),
    ).is_true()


def test_part_union_is_last_writer_wins(tmp_path: Path) -> None:
    """Later parts replace the same ``(path, hash)`` entry."""
    first = ReviewState(
        coverage=(CoverageRecord("a.py", "h1", reviewed_sha="old", round=1),),
        repo="lgtm-hq/py-lintro",
        pr_number=1,
    )
    second = ReviewState(
        coverage=(CoverageRecord("a.py", "h1", reviewed_sha="new", round=2),),
        repo="lgtm-hq/py-lintro",
        pr_number=1,
    )
    write_state_part(state=first, directory=tmp_path, sequence=1)
    write_state_part(state=second, directory=tmp_path, sequence=2, final=True)
    loaded = load_ci_state(
        directory=tmp_path,
        repo="lgtm-hq/py-lintro",
        pr_number=1,
    )
    assert_that(loaded.coverage[0].reviewed_sha).is_equal_to("new")
    assert_that(loaded.coverage[0].round).is_equal_to(2)


def test_wrong_repo_state_is_ignored(tmp_path: Path) -> None:
    """Forged or cross-repo state can only cause extra review."""
    write_state_part(
        state=ReviewState(
            coverage=(CoverageRecord("a.py", "h"),),
            repo="evil/other",
            pr_number=1,
        ),
        directory=tmp_path,
        sequence=1,
        final=True,
    )
    loaded = load_ci_state(
        directory=tmp_path,
        repo="lgtm-hq/py-lintro",
        pr_number=1,
    )
    assert_that(loaded.coverage).is_empty()


def test_legacy_sticky_never_seeds_coverage() -> None:
    """Migration copies findings/runs only."""
    from lintro.ai.review.review_state_codec import legacy_state_block

    blob = legacy_state_block(
        state=ReviewState(version=2, runs=(), findings=()),
    )
    migrated = migrate_legacy_sticky(body=f"hello{blob}")
    assert_that(migrated.legacy).is_true()
    assert_that(migrated.coverage).is_empty()


def test_local_ledger_keys_pr_not_branch() -> None:
    """Local ``--pr`` runs key by PR number."""
    assert_that(local_ledger_key(pr_number=12, head_ref="feat/x")).is_equal_to(
        "pr-12",
    )
    assert_that(local_ledger_key(pr_number=None, head_ref="feat/x")).is_equal_to(
        "feat-x",
    )


def test_local_write_is_atomic(tmp_path: Path) -> None:
    """A local ledger write replaces the file in one step."""
    path = write_local_state(
        state=ReviewState(repo="lgtm-hq/py-lintro", pr_number=9),
        key="pr-9",
        directory=tmp_path,
    )
    loaded = load_local_state(
        key="pr-9",
        directory=tmp_path,
        repo="lgtm-hq/py-lintro",
        pr_number=9,
    )
    assert_that(path.is_file()).is_true()
    assert_that(loaded.pr_number).is_equal_to(9)


def test_python_import_parse_skips_broken_source() -> None:
    """Unparseable Python yields no edges."""
    assert_that(parse_python_imports(source="def (\n")).is_empty()


def test_hashes_for_diffs_and_coverage_counts() -> None:
    """Counters treat uncovered queued files as awaiting."""
    diffs = {"a.py": "+one\n", "b.py": "+two\n"}
    hashes = hashes_for_diffs(diffs=diffs)
    classified = classify_files(
        eligible_paths=("a.py", "b.py"),
        current_hashes=hashes,
        coverage=(),
    )
    counts = coverage_counts(classified=classified, reviewed_now=("a.py",))
    assert_that(counts.reviewed).is_equal_to(1)
    assert_that(counts.awaiting).is_equal_to(1)
    assert_that(counts.complete).is_false()


def test_union_states_merges_independent_paths() -> None:
    """Coverage entries for different keys are independent."""
    merged = union_states(
        (
            ReviewState(coverage=(CoverageRecord("a.py", "1"),)),
            ReviewState(coverage=(CoverageRecord("b.py", "2"),)),
        ),
    )
    paths = {record.path for record in merged.coverage}
    assert_that(paths).is_equal_to({"a.py", "b.py"})

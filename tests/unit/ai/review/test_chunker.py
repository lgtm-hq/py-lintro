"""Tests for review semantic chunking."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.ai.review.chunker import chunk_review_context
from lintro.ai.review.classifier import classify_changed_files
from lintro.ai.review.exceptions import ReviewContextError
from lintro.ai.review.group_labels import REL_SOURCE_TEST, REL_WORKFLOW_SCRIPT_TEST
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.path_utils import matches_test_for_source


def test_chunker_groups_workflow_script_and_test(
    sample_review_context: ReviewContext,
) -> None:
    """Workflow, referenced script, and bats test are grouped together."""
    classifications = classify_changed_files(files=sample_review_context.changed_files)
    result = chunk_review_context(
        context=sample_review_context,
        max_tokens=10_000,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).contains(
        ".github/workflows/ci.yml",
        "scripts/ci/run.sh",
        "scripts/ci/test_run.bats",
    )
    assert_that(workflow_group.relationship).is_equal_to(REL_WORKFLOW_SCRIPT_TEST)


def test_chunker_groups_source_and_test(
    sample_review_context: ReviewContext,
) -> None:
    """Source and paired test files are grouped together."""
    classifications = classify_changed_files(files=sample_review_context.changed_files)
    result = chunk_review_context(
        context=sample_review_context,
        max_tokens=10_000,
        classifications=classifications,
    )

    source_group = next(
        chunk for chunk in result.chunks if "src/lib/math.py" in chunk.files
    )
    assert_that(source_group.files).contains("src/lib/math.py", "tests/test_math.py")
    assert_that(source_group.relationship).is_equal_to(REL_SOURCE_TEST)


def test_chunker_samples_repetitive_changes(repetitive_unified_diff: str) -> None:
    """Identical repetitive diffs are sampled to three representative files."""
    changed_files = [
        ChangedFile(
            path=f"pkg/item{index}.py",
            status="modified",
            additions=1,
            deletions=0,
        )
        for index in range(6)
    ]
    context = ReviewContext(
        base_ref="base",
        head_ref="head",
        changed_files=changed_files,
        unified_diff=repetitive_unified_diff,
        pr_metadata=None,
    )
    classifications = classify_changed_files(files=changed_files)

    result = chunk_review_context(
        context=context,
        max_tokens=10_000,
        classifications=classifications,
        allow_omitted_files=True,
    )

    all_files = [path for chunk in result.chunks for path in chunk.files]
    assert_that(all_files).is_length(3)
    assert_that(result.skipped_files).is_length(3)
    assert_that(result.warnings).is_not_empty()
    assert_that(result.chunks[0].metadata_note).contains("sampled 3")


def test_chunker_splits_when_over_budget_without_dropping_files(
    sample_review_context: ReviewContext,
) -> None:
    """Large groups split into multiple chunks instead of dropping files."""
    classifications = classify_changed_files(files=sample_review_context.changed_files)
    result = chunk_review_context(
        context=sample_review_context,
        max_tokens=20,
        classifications=classifications,
    )

    original_paths = {file.path for file in sample_review_context.changed_files}
    chunked_paths = {path for chunk in result.chunks for path in chunk.files}
    assert_that(chunked_paths).is_equal_to(original_paths)
    assert_that(result.chunks).is_not_empty()


def test_chunker_orders_source_before_test_in_group_diff() -> None:
    """Production source diffs appear before paired Python tests in a chunk."""
    source_diff = (
        "diff --git a/src/foo.py b/src/foo.py\n"
        "+++ b/src/foo.py\n"
        "+implementation\n"
    )
    test_diff = (
        "diff --git a/tests/test_foo.py b/tests/test_foo.py\n"
        "+++ b/tests/test_foo.py\n"
        "+assert True\n"
    )
    context = ReviewContext(
        base_ref="base",
        head_ref="head",
        changed_files=[
            ChangedFile(
                path="tests/test_foo.py",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="src/foo.py",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
        unified_diff=source_diff + test_diff,
        pr_metadata=None,
    )
    classifications = classify_changed_files(files=context.changed_files)

    result = chunk_review_context(
        context=context,
        max_tokens=10_000,
        classifications=classifications,
    )

    paired_chunk = next(
        chunk
        for chunk in result.chunks
        if "src/foo.py" in chunk.files and "tests/test_foo.py" in chunk.files
    )
    assert_that(paired_chunk.diff.find("src/foo.py")).is_less_than(
        paired_chunk.diff.find("tests/test_foo.py"),
    )


def test_chunker_marks_truncation_when_single_file_exceeds_budget() -> None:
    """Single oversized file diffs are truncated with warnings."""
    large_diff = "diff --git a/big.py b/big.py\n" + "+++ b/big.py\n" + f"+{'x' * 400}\n"
    context = ReviewContext(
        base_ref="base",
        head_ref="head",
        changed_files=[
            ChangedFile(path="big.py", status="modified", additions=400, deletions=0),
        ],
        unified_diff=large_diff,
        pr_metadata=None,
    )
    classifications = classify_changed_files(files=context.changed_files)

    result = chunk_review_context(
        context=context,
        max_tokens=20,
        classifications=classifications,
    )

    assert_that(result.truncated).is_true()
    assert_that(result.warnings).is_not_empty()
    assert_that(result.chunks).is_length(1)
    assert_that(result.chunks[0].files).contains("big.py")


def test_chunker_raises_when_diff_unparseable() -> None:
    """Changed files without parseable diff sections raise ReviewContextError."""
    context = ReviewContext(
        base_ref="base",
        head_ref="head",
        changed_files=[
            ChangedFile(path="a.py", status="modified", additions=1, deletions=0),
        ],
        unified_diff="not a unified diff",
        pr_metadata=None,
    )
    classifications = classify_changed_files(files=context.changed_files)

    with pytest.raises(ReviewContextError, match="missing diff sections"):
        chunk_review_context(
            context=context,
            max_tokens=10_000,
            classifications=classifications,
        )


def test_workflow_group_does_not_pair_unrelated_tests() -> None:
    """Workflow groups only pair tests with scripts in the same group."""
    unified_diff = """\
diff --git a/.github/workflows/ci.yml b/.github/workflows/ci.yml
--- a/.github/workflows/ci.yml
+++ b/.github/workflows/ci.yml
@@ -1 +1,2 @@
 name: CI
+run: scripts/ci/run.sh
diff --git a/scripts/ci/run.sh b/scripts/ci/run.sh
--- a/scripts/ci/run.sh
+++ b/scripts/ci/run.sh
@@ -1 +1,2 @@
 #!/usr/bin/env bash
+echo run
diff --git a/tests/test_ci.py b/tests/test_ci.py
--- a/tests/test_ci.py
+++ b/tests/test_ci.py
@@ -1 +1,2 @@
 def test_ci():
+    assert True
"""
    context = ReviewContext(
        base_ref="base",
        head_ref="head",
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/ci/run.sh",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="tests/test_ci.py",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
        unified_diff=unified_diff,
        pr_metadata=None,
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=10_000,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).contains(
        ".github/workflows/ci.yml",
        "scripts/ci/run.sh",
    )
    assert_that(workflow_group.files).does_not_contain("tests/test_ci.py")


def test_workflow_group_ignores_stem_matched_unreferenced_scripts() -> None:
    """Scripts are not grouped by workflow filename stem alone."""
    unified_diff = """\
diff --git a/.github/workflows/ci.yml b/.github/workflows/ci.yml
--- a/.github/workflows/ci.yml
+++ b/.github/workflows/ci.yml
@@ -1 +1,2 @@
 name: CI
+run: echo noop
diff --git a/scripts/ci.sh b/scripts/ci.sh
--- a/scripts/ci.sh
+++ b/scripts/ci.sh
@@ -1 +1,2 @@
 #!/usr/bin/env bash
+echo ci
"""
    context = ReviewContext(
        base_ref="base",
        head_ref="head",
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/ci.sh",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
        unified_diff=unified_diff,
        pr_metadata=None,
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).contains(".github/workflows/ci.yml")
    assert_that(workflow_group.files).does_not_contain("scripts/ci.sh")


def test_matches_test_for_source_rejects_loose_substring_pairs() -> None:
    """Test paths must explicitly pair with a source stem, not substring-match."""
    assert_that(
        matches_test_for_source(test_path="tests/test_foobar.py", source_stem="foo"),
    ).is_false()
    assert_that(
        matches_test_for_source(test_path="tests/test_foo.py", source_stem="foo"),
    ).is_true()


@pytest.mark.parametrize(
    ("test_path", "source_stem"),
    [
        ("src/foo_test.rs", "foo"),
        ("src/foo.spec.tsx", "foo"),
        ("src/foo.test.jsx", "foo"),
        ("tests/test_foo.tsx", "foo"),
    ],
)
def test_matches_test_for_source_supports_extended_extensions(
    *,
    test_path: str,
    source_stem: str,
) -> None:
    """Source/test pairing recognizes common non-Python test naming patterns."""
    assert_that(
        matches_test_for_source(test_path=test_path, source_stem=source_stem),
    ).is_true()

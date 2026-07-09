"""Tests for review chunk sampling and token budgets."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.ai.review.chunker import (
    _hunk_signature,
    _prune_semantic_groups,
    chunk_review_context,
)
from lintro.ai.review.classifier import classify_changed_files
from lintro.ai.review.enums.review_context_error_code import ReviewContextErrorCode
from lintro.ai.review.exceptions import ReviewContextError
from lintro.ai.review.group_labels import (
    REL_DIRECTORY_PREFIX,
    REL_SINGLE_FILE,
    REL_SOURCE_TEST,
)
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.review_context import ReviewContext


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
    sampling_warning_messages = [
        warning
        for warning in result.warnings
        if "share identical diff hunks" in warning
    ]
    assert_that(sampling_warning_messages).is_length(1)
    assert_that(
        any("sampled 3" in (chunk.metadata_note or "") for chunk in result.chunks),
    ).is_true()


def test_prune_semantic_groups_preserves_multi_file_relationships() -> None:
    """Sampling should prune group membership without rebuilding relationships."""
    groups = [
        (["src/foo.py", "tests/test_foo.py"], REL_SOURCE_TEST),
        (["pkg/a.py", "pkg/b.py", "pkg/c.py"], REL_DIRECTORY_PREFIX),
    ]
    pruned = _prune_semantic_groups(
        groups=groups,
        remaining_paths={"src/foo.py", "tests/test_foo.py", "pkg/a.py"},
    )

    assert_that(pruned).is_length(2)
    assert_that(pruned[0][1]).is_equal_to(REL_SOURCE_TEST)
    assert_that(pruned[1]).is_equal_to((["pkg/a.py"], REL_SINGLE_FILE))


def test_chunker_preserves_source_test_group_after_repetitive_sampling() -> None:
    """Protected semantic groups survive repetitive-diff sampling."""
    hunk = """\
diff --git a/{name} b/{name}
index 1111111..2222222 100644
--- a/{name}
+++ b/{name}
@@ -1 +1,2 @@
 value = 1
+value = 2
"""
    repetitive_paths = [f"pkg/item{index}.py" for index in range(6)]
    changed_files = [
        ChangedFile(
            path="src/lib/math.py",
            status="modified",
            additions=1,
            deletions=0,
        ),
        ChangedFile(
            path="tests/lib/test_math.py",
            status="modified",
            additions=1,
            deletions=0,
        ),
        *(
            ChangedFile(path=path, status="modified", additions=1, deletions=0)
            for path in repetitive_paths
        ),
    ]
    unified_diff = (
        hunk.format(name="src/lib/math.py")
        + hunk.format(
            name="tests/lib/test_math.py",
        )
        + "".join(hunk.format(name=path) for path in repetitive_paths)
    )
    context = ReviewContext(
        base_ref="base",
        head_ref="head",
        changed_files=changed_files,
        unified_diff=unified_diff,
        pr_metadata=None,
    )
    classifications = classify_changed_files(files=changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=10_000,
        classifications=classifications,
        allow_omitted_files=True,
    )

    source_group = next(
        chunk for chunk in result.chunks if "src/lib/math.py" in chunk.files
    )
    assert_that(source_group.relationship).is_equal_to(REL_SOURCE_TEST)
    assert_that(source_group.files).contains("tests/lib/test_math.py")


def test_chunker_prefers_high_priority_files_when_sampling_repetitive_changes() -> None:
    """Repetitive sampling keeps higher-signal files instead of lexicographic paths."""
    hunk = """\
diff --git a/pkg/{name} b/pkg/{name}
index 1111111..2222222 100644
--- a/pkg/{name}
+++ b/pkg/{name}
@@ -1 +1,2 @@
 value = 1
+value = 2
"""
    paths = [
        "pkg/item1.py",
        "pkg/item2.py",
        "pkg/item3.py",
        "pkg/item4.py",
        "pkg/item5.py",
        "pkg/security_auth.py",
    ]
    unified_diff = "".join(
        hunk.format(name=path.removeprefix("pkg/")) for path in paths
    )
    changed_files = [
        ChangedFile(path=path, status="modified", additions=1, deletions=0)
        for path in paths
    ]
    context = ReviewContext(
        base_ref="base",
        head_ref="head",
        changed_files=changed_files,
        unified_diff=unified_diff,
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
    assert_that(all_files).contains("pkg/security_auth.py")
    assert_that(all_files).is_length(3)


def test_chunker_splits_when_over_budget_without_dropping_files(
    sample_review_context: ReviewContext,
) -> None:
    """Large groups split into multiple chunks instead of dropping files."""
    classifications = classify_changed_files(files=sample_review_context.changed_files)
    baseline = chunk_review_context(
        context=sample_review_context,
        max_tokens=10_000,
        classifications=classifications,
    )
    result = chunk_review_context(
        context=sample_review_context,
        max_tokens=20,
        classifications=classifications,
    )

    original_paths = {file.path for file in sample_review_context.changed_files}
    all_chunked_paths = [path for chunk in result.chunks for path in chunk.files]
    chunked_paths = set(all_chunked_paths)
    assert_that(all_chunked_paths).is_length(len(chunked_paths))
    assert_that(chunked_paths).is_equal_to(original_paths)
    assert_that(len(result.chunks)).is_greater_than(len(baseline.chunks))


def test_chunker_marks_truncation_when_single_file_exceeds_budget() -> None:
    """Single oversized file diffs are truncated with warnings."""
    large_diff = (
        "diff --git a/big.py b/big.py\n"
        "index 1111111..2222222 100644\n"
        "--- a/big.py\n"
        "+++ b/big.py\n"
        "@@ -1 +1 @@\n"
        "-x\n" + f"+{'x' * 400}\n"
    )
    context = ReviewContext(
        base_ref="base",
        head_ref="head",
        changed_files=[
            ChangedFile(path="big.py", status="modified", additions=1, deletions=1),
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

    with pytest.raises(
        ReviewContextError,
        match="No parseable diff sections",
    ) as exc_info:
        chunk_review_context(
            context=context,
            max_tokens=10_000,
            classifications=classifications,
        )
    assert_that(exc_info.value.code).is_equal_to(
        ReviewContextErrorCode.NO_PARSEABLE_DIFF,
    )


def test_hunk_signature_handles_surrogateescaped_paths() -> None:
    """Hunk signatures accept surrogateescaped filenames from git output."""
    signature = _hunk_signature(
        path="bad\udcff.py",
        diff_text="@@ -1 +1 @@\n-old\n+new\udcff\n",
    )

    assert_that(signature).matches(r"^[0-9a-f]{64}$")


@pytest.mark.parametrize("max_tokens", [0, -1])
def test_chunker_rejects_non_positive_max_tokens(
    sample_review_context: ReviewContext,
    max_tokens: int,
) -> None:
    """Invalid chunk budgets raise before diff parsing."""
    classifications = classify_changed_files(files=sample_review_context.changed_files)
    with pytest.raises(ReviewContextError) as exc_info:
        chunk_review_context(
            context=sample_review_context,
            max_tokens=max_tokens,
            classifications=classifications,
        )
    assert_that(exc_info.value.code).is_equal_to(
        ReviewContextErrorCode.INVALID_CHUNK_BUDGET,
    )


def test_chunker_raises_when_diff_contains_extra_paths() -> None:
    """Extra diff sections not listed in changed_files fail fast."""
    context = ReviewContext(
        base_ref="base",
        head_ref="head",
        changed_files=[
            ChangedFile(path="a.py", status="modified", additions=1, deletions=0),
        ],
        unified_diff=(
            "diff --git a/a.py b/a.py\n"
            "index 1111111..2222222 100644\n"
            "--- a/a.py\n"
            "+++ b/a.py\n"
            "@@ -1 +1 @@\n"
            "-a\n"
            "+b\n"
            "diff --git a/b.py b/b.py\n"
            "index 3333333..4444444 100644\n"
            "--- a/b.py\n"
            "+++ b/b.py\n"
            "@@ -1 +1 @@\n"
            "-x\n"
            "+y\n"
        ),
        pr_metadata=None,
    )
    classifications = classify_changed_files(files=context.changed_files)

    with pytest.raises(ReviewContextError) as exc_info:
        chunk_review_context(
            context=context,
            max_tokens=10_000,
            classifications=classifications,
        )
    assert_that(exc_info.value.code).is_equal_to(ReviewContextErrorCode.DIFF_DESYNC)

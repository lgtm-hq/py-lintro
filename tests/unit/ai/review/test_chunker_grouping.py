"""Tests for review semantic chunk grouping."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.ai.review.chunker import chunk_review_context
from lintro.ai.review.classifier import classify_changed_files
from lintro.ai.review.group_labels import REL_SOURCE_TEST, REL_WORKFLOW_SCRIPT_TEST
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.path_utils import matches_test_for_source
from tests.unit.ai.review.review_fixtures import (
    load_review_fixture,
    make_review_context,
)


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
    assert_that(source_group.files).contains(
        "src/lib/math.py",
        "tests/lib/test_math.py",
    )
    assert_that(source_group.relationship).is_equal_to(REL_SOURCE_TEST)


def test_workflow_group_does_not_pair_unrelated_tests() -> None:
    """Workflow groups only pair tests with scripts in the same group."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_workflow_group_does_not_pair_unrelated_tests.diff",
        ),
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
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_workflow_group_ignores_stem_matched_unreferenced_scripts.diff",
        ),
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
    assert_that(result.warnings).contains(
        "Script scripts/ci.sh changed alongside workflows but is not referenced "
        "in any changed workflow diff; grouped separately.",
    )


def test_workflow_group_ignores_removed_script_references() -> None:
    """Removed workflow references do not group scripts in the post-change state."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_workflow_group_ignores_removed_script_references.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=1,
            ),
            ChangedFile(
                path="scripts/ci.sh",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
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
    assert_that(workflow_group.files).does_not_contain("scripts/ci.sh")


def test_workflow_group_does_not_warn_on_unreferenced_bats_tests() -> None:
    """BATS test files changed alongside workflows are not treated as scripts."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_workflow_group_does_not_warn_on_unreferenced_bats_tests.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="tests/run.bats",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
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
    assert_that(workflow_group.files).does_not_contain("tests/run.bats")
    assert_that(result.warnings).does_not_contain(
        "Script tests/run.bats changed alongside workflows but is not referenced "
        "in any changed workflow diff; grouped separately.",
    )


def test_workflow_group_does_not_warn_on_non_executable_script_assets() -> None:
    """Non-executable assets under scripts/ do not emit script reference warnings."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_workflow_group_does_not_warn_on_non_executable_script_assets.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path="scripts/config.json",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    assert_that(result.warnings).does_not_contain(
        "Script scripts/config.json changed alongside workflows but is not referenced "
        "in any changed workflow diff; grouped separately.",
    )


def test_workflow_group_does_not_warn_on_local_action_docs() -> None:
    """Non-executable files under local action directories are not script-like."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_workflow_group_does_not_warn_on_local_action_docs.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path=".github/actions/setup/README.md",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)
    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    assert_that(result.warnings).does_not_contain(
        "Script .github/actions/setup/README.md changed alongside workflows but "
        "is not referenced in any changed workflow diff; grouped separately.",
    )


def test_chunker_orders_source_before_test_in_group_diff() -> None:
    """Production source diffs appear before paired Python tests in a chunk."""
    unified_diff = load_review_fixture(
        "chunk_source_test_ordering_test.diff",
    ) + load_review_fixture("chunk_source_test_ordering_source.diff")
    context = make_review_context(
        unified_diff=unified_diff,
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
    source_index = paired_chunk.diff.index("diff --git a/src/foo.py")
    test_index = paired_chunk.diff.index("diff --git a/tests/test_foo.py")
    assert_that(source_index).is_less_than(test_index)


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

"""End-to-end tests for review preparation pipeline."""

from __future__ import annotations

from assertpy import assert_that

from lintro.ai.review.context import parse_changed_files
from lintro.ai.review.group_labels import REL_WORKFLOW_SCRIPT_TEST
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.pipeline import prepare_review_chunks


def test_prepare_review_chunks_from_parsed_git_output(
    sample_unified_diff: str,
) -> None:
    """Pipeline covers every path present in a parsed unified diff."""
    name_status = (
        "M\t.github/workflows/ci.yml\n"
        "M\tscripts/ci/run.sh\n"
        "M\tscripts/ci/test_run.bats\n"
        "M\tsrc/lib/math.py\n"
        "M\ttests/lib/test_math.py\n"
    )
    numstat = (
        "1\t0\t.github/workflows/ci.yml\n"
        "1\t0\tscripts/ci/run.sh\n"
        "1\t0\tscripts/ci/test_run.bats\n"
        "1\t0\tsrc/lib/math.py\n"
        "1\t0\ttests/lib/test_math.py\n"
    )
    changed_files = parse_changed_files(name_status=name_status, numstat=numstat)
    context = ReviewContext(
        base_ref="base",
        head_ref="head",
        changed_files=changed_files,
        unified_diff=sample_unified_diff,
        pr_metadata=None,
    )

    result = prepare_review_chunks(context=context, max_tokens=10_000)

    chunked_paths = {path for chunk in result.chunks for path in chunk.files}
    assert_that(chunked_paths).is_equal_to({file.path for file in changed_files})
    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.relationship).is_equal_to(REL_WORKFLOW_SCRIPT_TEST)

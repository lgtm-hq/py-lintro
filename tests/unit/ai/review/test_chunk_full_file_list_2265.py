"""Every chunk prompt carries the whole PR's changed-file list (issue #2265).

A chunk that only sees its own files reads the rest of the pull request from
disk at the base commit and concludes, in good faith, that they were never
updated. These tests pin the prevention step: both chunk prompt builders list
every path the pull request changed, mark the chunk's own files so the two sets
stay distinguishable, and warn that the unmarked files are stale on disk.
"""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.ai.prompts.review import CHUNK_FILE_MARKER
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.pr_metadata import PRMetadata
from lintro.ai.review.models.review_chunk import ReviewChunk
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.orchestrator import (
    build_git_native_review_prompt,
    build_review_prompt,
)

_CHUNK_PATH = "scripts/migrate_docs_content.py"
_OTHER_PATHS = (
    "tests/test_migrate_docs_content.py",
    "docs/migration.md",
)


def _make_context() -> ReviewContext:
    """Build a review context whose PR changes three files.

    Returns:
        A review context spanning the chunk file and two files outside it.
    """
    paths = (_CHUNK_PATH, *_OTHER_PATHS)
    return ReviewContext(
        base_ref="main",
        head_ref="feature",
        changed_files=[
            ChangedFile(
                path=path,
                status="modified",
                additions=4,
                deletions=1,
            )
            for path in paths
        ],
        unified_diff=f"diff --git a/{_CHUNK_PATH} b/{_CHUNK_PATH}\n",
        pr_metadata=PRMetadata(
            title="Migrate docs content",
            body="Routine change.",
            number=1914,
            repo="owner/repo",
        ),
    )


def _make_chunk() -> ReviewChunk:
    """Build a chunk covering only the script, not its test or docs.

    Returns:
        A single-file review chunk.
    """
    return ReviewChunk(
        id=1,
        files=[_CHUNK_PATH],
        diff=f"diff --git a/{_CHUNK_PATH} b/{_CHUNK_PATH}\n+x = 1\n",
        relationship="single-file",
    )


def _build(*, builder_name: str) -> str:
    """Render a chunk user prompt with the named builder.

    Args:
        builder_name: Either ``diff`` or ``git_native``.

    Returns:
        The rendered user prompt.
    """
    builder = {
        "diff": build_review_prompt,
        "git_native": build_git_native_review_prompt,
    }[builder_name]
    _system, user_prompt = builder(
        chunk=_make_chunk(),
        context=_make_context(),
        checklist_text="1. [logic-bug] Example question?",
        checklist_count=1,
        interaction_paths="(none)",
    )
    return user_prompt


@pytest.mark.parametrize(
    "builder_name",
    ["diff", "git_native"],
    ids=["builder=diff", "builder=git_native"],
)
def test_chunk_prompt_lists_every_changed_path_of_the_pr(
    builder_name: str,
) -> None:
    """Both chunk prompt builders name every path the PR changed.

    Args:
        builder_name: Prompt builder under test.
    """
    prompt = _build(builder_name=builder_name)

    listed = [line for line in prompt.splitlines() if line.startswith("- `")]

    # Each path must be a list entry of its own, not merely visible somewhere
    # in the chunk diff.
    for path in (_CHUNK_PATH, *_OTHER_PATHS):
        assert_that([line for line in listed if f"`{path}`" in line]).is_not_empty()


@pytest.mark.parametrize(
    "builder_name",
    ["diff", "git_native"],
    ids=["builder=diff", "builder=git_native"],
)
def test_chunk_prompt_marks_only_the_chunks_own_files(
    builder_name: str,
) -> None:
    """The chunk's own files carry the marker and the others do not.

    Args:
        builder_name: Prompt builder under test.
    """
    prompt = _build(builder_name=builder_name)
    marked = [line for line in prompt.splitlines() if CHUNK_FILE_MARKER in line]
    listed_paths = [line for line in marked if line.startswith("- `")]

    assert_that(listed_paths).is_length(1)
    assert_that(listed_paths[0]).contains(_CHUNK_PATH)
    for path in _OTHER_PATHS:
        assert_that(listed_paths[0]).does_not_contain(path)


@pytest.mark.parametrize(
    "builder_name",
    ["diff", "git_native"],
    ids=["builder=diff", "builder=git_native"],
)
def test_chunk_prompt_warns_that_other_files_are_stale_on_disk(
    builder_name: str,
) -> None:
    """The prompt tells the model not to trust on-disk copies of other files.

    Args:
        builder_name: Prompt builder under test.
    """
    prompt = " ".join(_build(builder_name=builder_name).split())

    assert_that(prompt).contains("stale base-commit version")
    assert_that(prompt).contains(
        "never treat it as evidence that such a file was not updated",
    )


@pytest.mark.parametrize(
    "builder_name",
    ["diff", "git_native"],
    ids=["builder=diff", "builder=git_native"],
)
def test_chunk_prompt_keeps_the_chunk_scoped_changed_files_section(
    builder_name: str,
) -> None:
    """The chunk-scoped ``changed_files`` header still counts only the chunk.

    Args:
        builder_name: Prompt builder under test.
    """
    prompt = _build(builder_name=builder_name)

    assert_that(prompt).contains("**Changed files (1):**")


@pytest.mark.parametrize(
    "builder_name",
    ["diff", "git_native"],
    ids=["builder=diff", "builder=git_native"],
)
def test_full_pr_file_list_goes_through_redaction(
    builder_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The full-PR path list passes the same redaction choke point as the diff.

    Args:
        builder_name: Prompt builder under test.
        monkeypatch: Pytest monkeypatch fixture.
    """
    import lintro.ai.review.orchestrator as orchestrator_module

    def _tagged(*, text: str, source: str) -> str:
        return f"[redacted:{source}]{text}"

    monkeypatch.setattr(orchestrator_module, "redact_prompt_text", _tagged)

    prompt = _build(builder_name=builder_name)
    marked = [line for line in prompt.splitlines() if CHUNK_FILE_MARKER in line]

    # The full-PR list is rendered as one redacted block: its first line
    # carries the tag and every PR path, marked or not, sits inside it.
    assert_that(marked).is_not_empty()
    # Each redacted block is the list itself, up to the first blank line.
    lists = [
        block.split("\n\n")[0] for block in prompt.split("[redacted:changed files]")[1:]
    ]
    full_list = [
        entry
        for entry in lists
        if any(CHUNK_FILE_MARKER in line for line in entry.splitlines())
    ]
    assert_that(full_list).is_length(1)
    for path in (_CHUNK_PATH, *_OTHER_PATHS):
        assert_that(full_list[0]).contains(f"- `{path}`")

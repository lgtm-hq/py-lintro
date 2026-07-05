"""Tests for review diff parsing helpers."""

from __future__ import annotations

from typing import Any, cast

import pytest
from assertpy import assert_that

from lintro.ai.review.context import (
    parse_changed_files,
    split_unified_diff_by_file,
    unified_diff_preamble,
    validate_review_context_diff,
)
from lintro.ai.review.context.pr_metadata import (
    _parse_changed_files_from_diff,
    _parse_pr_view_json,
)
from lintro.ai.review.enums.changed_file_status import ChangedFileStatus
from lintro.ai.review.enums.review_context_error_code import ReviewContextErrorCode
from lintro.ai.review.exceptions import ReviewContextError
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.review_context import ReviewContext


def test_split_unified_diff_by_file_returns_sections(sample_unified_diff: str) -> None:
    """Unified diff splitting returns one section per changed file."""
    sections = split_unified_diff_by_file(unified_diff=sample_unified_diff)
    assert_that(sections).contains_key("scripts/ci/run.sh")
    assert_that(sections["scripts/ci/run.sh"]).contains('echo "running"')


def test_unified_diff_preamble_preserves_leading_status_lines() -> None:
    """Leading bytes before the first diff header are treated as preamble."""
    diff_text = (
        "==> Fetching pull request\n\n"
        "diff --git a/a.py b/a.py\n"
        "+++ b/a.py\n"
        "+a\n"
    )
    assert_that(unified_diff_preamble(unified_diff=diff_text)).is_equal_to(
        "==> Fetching pull request\n\n",
    )


def test_changed_file_rejects_negative_line_counts() -> None:
    """Changed file metadata rejects negative diff line counts."""
    with pytest.raises(ValueError, match="non-negative"):
        ChangedFile(path="a.py", status="modified", additions=-1, deletions=0)


def test_changed_file_rejects_previous_path_for_non_rename() -> None:
    """previous_path is only valid for renamed or copied files."""
    with pytest.raises(ValueError, match="only allowed for renamed or copied"):
        ChangedFile(
            path="a.py",
            status="modified",
            additions=1,
            deletions=0,
            previous_path="old.py",
        )


def test_validate_review_context_diff_rejects_unparseable_diff_without_files() -> None:
    """Non-empty diffs that fail to parse raise before review proceeds."""
    context = ReviewContext(
        base_ref="abc",
        head_ref="def",
        changed_files=[],
        unified_diff="not a valid unified diff header\n",
        pr_metadata=None,
    )

    with pytest.raises(ReviewContextError) as exc_info:
        validate_review_context_diff(context=context)
    assert_that(exc_info.value.code).is_equal_to(
        ReviewContextErrorCode.NO_PARSEABLE_DIFF,
    )


def test_validate_review_context_diff_rejects_parseable_diff_without_files() -> None:
    """Parseable diffs without changed-file metadata are a desync."""
    context = ReviewContext(
        base_ref="abc",
        head_ref="def",
        changed_files=[],
        unified_diff=(
            "diff --git a/a.py b/a.py\n"
            "--- a/a.py\n"
            "+++ b/a.py\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
        ),
        pr_metadata=None,
    )

    with pytest.raises(ReviewContextError) as exc_info:
        validate_review_context_diff(context=context)
    assert_that(exc_info.value.code).is_equal_to(ReviewContextErrorCode.DIFF_DESYNC)


@pytest.mark.parametrize(
    ("name_status", "numstat", "expected_path", "expected_status", "expected_previous"),
    [
        pytest.param(
            "R100\told_name.py\tnew_name.py\n",
            "1\t0\tnew_name.py\n",
            "new_name.py",
            "renamed",
            "old_name.py",
            id="rename_status",
        ),
        pytest.param(
            "A\tadded.py\n",
            "3\t0\tadded.py\n",
            "added.py",
            "added",
            None,
            id="added_status",
        ),
        pytest.param(
            "C100\told.py\tnew.py\n",
            "1\t0\tnew.py\n",
            "new.py",
            "copied",
            "old.py",
            id="copied_status",
        ),
        pytest.param(
            "T\tfile.py\n",
            "1\t1\tfile.py\n",
            "file.py",
            "type-changed",
            None,
            id="type_changed_status",
        ),
    ],
)
def test_parse_changed_files_normalizes_git_status(
    *,
    name_status: str,
    numstat: str,
    expected_path: str,
    expected_status: str,
    expected_previous: str | None,
) -> None:
    """Git name-status codes are parsed into normalized changed-file entries."""
    changed_files = parse_changed_files(name_status=name_status, numstat=numstat)

    assert_that(changed_files).is_length(1)
    assert_that(changed_files[0].path).is_equal_to(expected_path)
    assert_that(changed_files[0].status).is_equal_to(ChangedFileStatus(expected_status))
    assert_that(changed_files[0].previous_path).is_equal_to(expected_previous)


def test_split_unified_diff_by_file_supports_quoted_paths() -> None:
    """Quoted diff headers parse paths containing spaces."""
    diff_text = (
        'diff --git "a/foo bar.py" "b/foo bar.py"\n' "+++ b/foo bar.py\n" "+change\n"
    )
    sections = split_unified_diff_by_file(unified_diff=diff_text)
    assert_that(sections).contains_key("foo bar.py")


def test_split_unified_diff_by_file_supports_unquoted_spaced_paths() -> None:
    """Unquoted diff headers parse paths containing spaces."""
    diff_text = (
        "diff --git a/foo bar.py b/foo bar.py\n" "+++ b/foo bar.py\n" "+change\n"
    )
    sections = split_unified_diff_by_file(unified_diff=diff_text)
    assert_that(sections).contains_key("foo bar.py")


def test_split_unified_diff_by_file_unescapes_doubled_backslashes() -> None:
    """Quoted headers preserve literal backslash-n sequences in filenames."""
    diff_text = (
        'diff --git "a/foo\\\\nbar.py" "b/foo\\\\nbar.py"\n'
        "@@ -0,0 +1 @@\n"
        "+change\n"
    )
    sections = split_unified_diff_by_file(unified_diff=diff_text)
    assert_that(sections).contains_key("foo\\nbar.py")


def test_split_unified_diff_by_file_unescapes_quoted_double_quotes() -> None:
    """Quoted headers parse paths containing escaped double quotes."""
    diff_text = (
        'diff --git "a/he\\"said.py" "b/he\\"said.py"\n'
        '+++ "b/he\\"said.py"\n'
        "+change\n"
    )
    sections = split_unified_diff_by_file(unified_diff=diff_text)
    assert_that(sections).contains_key('he"said.py')


def test_parse_changed_files_supports_nul_delimited_metadata() -> None:
    """NUL-delimited git metadata is parsed without trimming path whitespace."""
    name_status = "M\0src/a.py \0"
    numstat = "1\t-\tsrc/a.py \0"
    changed_files = parse_changed_files(name_status=name_status, numstat=numstat)
    assert_that(changed_files).is_length(1)
    assert_that(changed_files[0].path).is_equal_to("src/a.py ")
    assert_that(changed_files[0].additions).is_equal_to(1)
    assert_that(changed_files[0].deletions).is_equal_to(0)


def test_parse_changed_files_parses_multi_file_numstat_z() -> None:
    """NUL-delimited numstat assigns line counts per changed file."""
    name_status = "M\0a.py\0M\0b.py\0"
    numstat = "1\t0\ta.py\0" + "2\t3\tb.py\0"
    changed_files = parse_changed_files(name_status=name_status, numstat=numstat)

    assert_that(changed_files).is_length(2)
    by_path = {file.path: file for file in changed_files}
    assert_that(by_path["a.py"].additions).is_equal_to(1)
    assert_that(by_path["a.py"].deletions).is_equal_to(0)
    assert_that(by_path["b.py"].additions).is_equal_to(2)
    assert_that(by_path["b.py"].deletions).is_equal_to(3)


def test_parse_changed_files_matches_brace_compressed_numstat_paths() -> None:
    """Brace-compressed rename paths in numstat map to the new file path."""
    name_status = "R100\tsrc/old.py\tsrc/new.py\n"
    numstat = "1\t0\tsrc/{old.py => new.py}\n"
    changed_files = parse_changed_files(name_status=name_status, numstat=numstat)
    assert_that(changed_files[0].path).is_equal_to("src/new.py")
    assert_that(changed_files[0].previous_path).is_equal_to("src/old.py")
    assert_that(changed_files[0].additions).is_equal_to(1)


def test_parse_changed_files_parses_numstat_z_rename_records() -> None:
    """NUL-delimited numstat rename records map counts to the new path."""
    name_status = "R100\0old.py\0new.py\0"
    numstat = "1\t0\t\0old.py\0new.py\0"
    changed_files = parse_changed_files(name_status=name_status, numstat=numstat)

    assert_that(changed_files).is_length(1)
    assert_that(changed_files[0].path).is_equal_to("new.py")
    assert_that(changed_files[0].previous_path).is_equal_to("old.py")
    assert_that(changed_files[0].additions).is_equal_to(1)
    assert_that(changed_files[0].deletions).is_equal_to(0)


def test_parse_changed_files_from_diff_sets_previous_path_on_rename() -> None:
    """Unified diff rename sections populate ``previous_path`` metadata."""
    unified = (
        "diff --git a/old.py b/new.py\n"
        "rename from old.py\n"
        "rename to new.py\n"
        "--- a/old.py\n"
        "+++ b/new.py\n"
        "@@ -1 +1 @@\n"
        "-x\n"
        "+y\n"
    )
    changed_files = _parse_changed_files_from_diff(unified_diff=unified)

    assert_that(changed_files).is_length(1)
    assert_that(changed_files[0].path).is_equal_to("new.py")
    assert_that(changed_files[0].previous_path).is_equal_to("old.py")
    assert_that(changed_files[0].status).is_equal_to(ChangedFileStatus.RENAMED)


def test_parse_changed_files_rejects_malformed_rename_without_destination() -> None:
    """Truncated rename records fail parsing instead of silently degrading."""
    with pytest.raises(ReviewContextError) as exc_info:
        parse_changed_files(
            name_status="R100\told.py\n",
            numstat="1\t0\told.py\n",
        )
    assert_that(exc_info.value.code).is_equal_to(
        ReviewContextErrorCode.GIT_OUTPUT_PARSE_FAILED,
    )


def test_parse_changed_files_from_diff_treats_executable_bit_change_as_modified() -> (
    None
):
    """Regular-file permission flips stay MODIFIED instead of TYPE_CHANGED."""
    unified = (
        "diff --git a/file.py b/file.py\n"
        "old mode 100644\n"
        "new mode 100755\n"
        "--- a/file.py\n"
        "+++ b/file.py\n"
    )
    changed_files = _parse_changed_files_from_diff(unified_diff=unified)

    assert_that(changed_files).is_length(1)
    assert_that(changed_files[0].status).is_equal_to(ChangedFileStatus.MODIFIED)


def test_parse_changed_files_from_diff_detects_type_changed() -> None:
    """Unified diff object-type changes map to TYPE_CHANGED status."""
    unified = (
        "diff --git a/link b/link\n"
        "old mode 100644\n"
        "new mode 120000\n"
        "--- a/link\n"
        "+++ b/link\n"
    )
    changed_files = _parse_changed_files_from_diff(unified_diff=unified)

    assert_that(changed_files).is_length(1)
    assert_that(changed_files[0].status).is_equal_to(ChangedFileStatus.TYPE_CHANGED)


def test_unquote_git_path_decodes_octal_and_c_escapes() -> None:
    """Quoted diff headers decode to paths matching NUL-delimited metadata."""
    diff_text = (
        'diff --git "a/caf\\303\\251.py" "b/caf\\303\\251.py"\n'
        "+++ b/café.py\n"
        'diff --git "a/foo\\rbar.py" "b/foo\\rbar.py"\n'
        "+++ b/foo\\rbar.py\n"
    )
    sections = split_unified_diff_by_file(unified_diff=diff_text)
    assert_that(sections).contains_key("café.py")
    assert_that(sections).contains_key("foo\rbar.py")


def test_unquote_git_path_treats_invalid_octal_as_literal() -> None:
    """Octal escapes above 0xFF decode as literal backslashes, not ValueError."""
    diff_text = (
        'diff --git "a/bad\\400name.py" "b/bad\\400name.py"\n' "+++ b/bad\\400name.py\n"
    )
    sections = split_unified_diff_by_file(unified_diff=diff_text)
    assert_that(sections).contains_key("bad\\400name.py")


def test_split_unified_diff_by_file_prefers_plus_plus_path() -> None:
    """Section keys prefer the ``+++ b/`` path when the git header is ambiguous."""
    diff_text = (
        "diff --git a/weird b/path b/weird b/path\n"
        "+++ b/src/real/module.py\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
    )
    sections = split_unified_diff_by_file(unified_diff=diff_text)
    assert_that(sections).contains_key("src/real/module.py")


def test_split_unified_diff_by_file_uses_minus_header_for_deletions() -> None:
    """Deleted files prefer ``--- a/`` when ``+++`` is ``/dev/null``."""
    diff_text = (
        "diff --git a/weird b/path b/weird b/path\n"
        "deleted file mode 100644\n"
        "--- a/weird b/path\n"
        "+++ /dev/null\n"
        "@@ -1 +1 @@\n"
        "-old\n"
    )
    sections = split_unified_diff_by_file(unified_diff=diff_text)
    assert_that(sections).contains_key("weird b/path")


def test_split_unified_diff_by_file_concatenates_duplicate_sections() -> None:
    """Multiple diff sections for one path are preserved in order."""
    diff_text = (
        "diff --git a/a.py b/a.py\n"
        "+++ b/a.py\n"
        "@@ -1 +1,2 @@\n"
        " line\n"
        "+first\n"
        "diff --git a/a.py b/a.py\n"
        "+++ b/a.py\n"
        "@@ -2 +3,4 @@\n"
        " line\n"
        "+second\n"
    )
    sections = split_unified_diff_by_file(unified_diff=diff_text)
    assert_that(sections).contains_key("a.py")
    assert_that(sections["a.py"]).contains("+first")
    assert_that(sections["a.py"]).contains("+second")
    assert_that(sections["a.py"].index("+first")).is_less_than(
        sections["a.py"].index("+second"),
    )
    assert_that(sections["a.py"].count("diff --git a/a.py b/a.py")).is_equal_to(2)


def test_parse_changed_files_from_diff_resets_hunk_state_between_sections() -> None:
    """Concatenated diff sections do not inflate addition/deletion counts."""
    diff_text = (
        "diff --git a/a.py b/a.py\n"
        "+++ b/a.py\n"
        "@@ -1 +1,2 @@\n"
        " line\n"
        "+first\n"
        "diff --git a/a.py b/a.py\n"
        "+++ b/a.py\n"
        "@@ -2 +3,4 @@\n"
        " line\n"
        "+second\n"
    )
    changed_files = _parse_changed_files_from_diff(unified_diff=diff_text)
    assert_that(changed_files).is_length(1)
    assert_that(changed_files[0].additions).is_equal_to(2)
    assert_that(changed_files[0].deletions).is_equal_to(0)


def test_review_context_error_coerces_string_code() -> None:
    """ReviewContextError accepts enum value strings like ChangedFile status."""
    error = ReviewContextError("missing changes", code="no-changes")
    assert_that(error.code).is_equal_to(ReviewContextErrorCode.NO_CHANGES)


def test_review_context_error_rejects_invalid_code_type() -> None:
    """Non-string, non-enum codes raise TypeError."""
    with pytest.raises(TypeError, match="ReviewContextErrorCode"):
        ReviewContextError("bad code", code=cast(Any, 123))


def test_parse_changed_files_supports_newlines_in_numstat_z_paths() -> None:
    """NUL-delimited numstat paths may contain embedded newlines."""
    path = "line1\nline2"
    numstat = f"1\t0\t{path}\0"
    name_status = f"A\0{path}\0"
    changed_files = parse_changed_files(name_status=name_status, numstat=numstat)
    assert_that(changed_files).is_length(1)
    assert_that(changed_files[0].path).is_equal_to(path)


def test_pr_metadata_missing_base_uses_head_repo() -> None:
    """Resolve the repository from head when baseRepository is absent.

    When ``baseRepository`` is missing but ``headRepository`` carries a valid
    ``nameWithOwner``, the parsed head repository must be used as the fallback
    instead of raising "Could not determine repository".
    """
    import json

    payload = json.dumps(
        {
            "number": 42,
            "title": "Example PR",
            "baseRefOid": "a" * 40,
            "headRefOid": "b" * 40,
            "headRepository": {"nameWithOwner": "octo/head-fork"},
            "body": "Body text",
        },
    )

    metadata, base_ref, head_ref = _parse_pr_view_json(
        payload=payload,
        repo_override=None,
    )

    assert_that(metadata.repo).is_equal_to("octo/head-fork")
    assert_that(metadata.head_repo).is_equal_to("octo/head-fork")
    assert_that(base_ref).is_equal_to("a" * 40)
    assert_that(head_ref).is_equal_to("b" * 40)

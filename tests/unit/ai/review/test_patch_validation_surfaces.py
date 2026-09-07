"""Surface tests for suggested-patch validation (#2101).

Covers the head-content reader the validator is injected with — including the
``gh`` fetch path used in ``--pr`` mode — and the terminal and sticky-comment
surfaces that must state a drop rather than let it pass silently.
"""

from __future__ import annotations

from subprocess import (
    CompletedProcess,
)  # nosec B404 - subprocess is only referenced to build mock return values
from typing import Any
from unittest.mock import MagicMock, patch

from assertpy import assert_that
from rich.console import Console

from lintro.ai.review.display import render_review_terminal
from lintro.ai.review.enums.suggestion_drop_reason import SuggestionDropReason
from lintro.ai.review.inline_fix import finding_suggested_change
from lintro.ai.review.models.pr_metadata import PRMetadata
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.models.review_finding import ReviewFinding, Severity
from lintro.ai.review.models.review_metadata import ReviewMetadata
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.suggested_change import SuggestedChange
from lintro.ai.review.patch_validation import validate_suggested_patches
from lintro.ai.review.sticky.sections import _suggestion_drops_row


def _completed(*, stdout: str = "", returncode: int = 0) -> CompletedProcess[str]:
    """Build a completed-process stub.

    Args:
        stdout: Standard output the stub reports.
        returncode: Exit status the stub reports.

    Returns:
        The stubbed completed process.
    """
    return CompletedProcess(args=["stub"], returncode=returncode, stdout=stdout)


def _pr_context() -> ReviewContext:
    """Build a ``--pr`` mode review context.

    Returns:
        A context whose head ref and head repository drive the gh fallback.
    """
    return ReviewContext(
        base_ref="base",
        head_ref="deadbeef",
        changed_files=[],
        unified_diff="",
        pr_metadata=PRMetadata(
            number=7,
            title="t",
            body="b",
            repo="lgtm-hq/py-lintro",
            head_repo="contributor/py-lintro",
        ),
    )


def _head_reader(*, context: ReviewContext) -> Any:
    """Build a head-file reader from the live collection module.

    ``test_package_exports`` reloads review modules, so a reader captured at
    import time can close over a stale module whose ``_run_git`` the patches
    below never reach. Resolving it per call keeps the patch target and the
    function under test in the same module object.

    Args:
        context: Review context to build the reader for.

    Returns:
        The head-file reader callable.
    """
    from lintro.ai.review.context.collection import make_head_file_reader

    return make_head_file_reader(context=context)


def _finding(**overrides: Any) -> ReviewFinding:
    """Build a review finding whose suggestion was dropped.

    Args:
        **overrides: Fields to override on the base finding.

    Returns:
        The constructed finding.
    """
    base: dict[str, Any] = {
        "severity": Severity.P2,
        "category": "logic-bug",
        "file": "src/app.py",
        "line": 3,
        "title": "Stale hunk",
        "description": "d",
        "cause": "c",
        "fix": "call it with a default",
        "confidence": "high",
        "suggestion_dropped": SuggestionDropReason.STALE_ANCHOR,
    }
    base.update(overrides)
    return ReviewFinding(**base)


def _result(*findings: ReviewFinding) -> ReviewResult:
    """Build a review result carrying the given findings.

    Args:
        *findings: Findings to attach.

    Returns:
        The constructed result.
    """
    return ReviewResult(
        metadata=ReviewMetadata(
            model="m",
            provider="p",
            context_window=1,
            depth=1,
            chunks_total=1,
            chunks_current=1,
            files_reviewed=1,
            files_total=1,
            checklist_items=1,
        ),
        summary="s",
        findings=tuple(findings),
    )


@patch("lintro.ai.review.context.collection._run_gh")
@patch("lintro.ai.review.context.collection._run_git")
def test_pr_mode_reader_falls_back_to_gh_contents_api(
    mock_run_git: MagicMock,
    mock_run_gh: MagicMock,
) -> None:
    """A blob missing locally is fetched from the head repo via ``gh``.

    Args:
        mock_run_git: Patched git runner reporting a missing object.
        mock_run_gh: Patched gh runner serving the file content.
    """
    mock_run_git.return_value = _completed(returncode=1)
    mock_run_gh.return_value = _completed(stdout="line one\nline two\n")

    read = _head_reader(context=_pr_context())

    assert_that(read("src/app.py")).is_equal_to("line one\nline two\n")
    api_args = mock_run_gh.call_args.kwargs["args"]
    assert_that(api_args[0]).is_equal_to("api")
    assert_that(api_args[1]).contains("contributor/py-lintro")
    assert_that(api_args[1]).contains("ref=deadbeef")


@patch("lintro.ai.review.context.collection._run_gh")
@patch("lintro.ai.review.context.collection._run_git")
def test_pr_mode_reader_memoizes_each_path(
    mock_run_git: MagicMock,
    mock_run_gh: MagicMock,
) -> None:
    """Two findings on one file cost a single fetch.

    Args:
        mock_run_git: Patched git runner reporting a missing object.
        mock_run_gh: Patched gh runner serving the file content.
    """
    fetches: list[str] = []

    def _serve(*_args: object, **kwargs: object) -> object:
        """Serve the file once and record the fetch.

        Args:
            *_args: Ignored positional arguments.
            **kwargs: Runner keyword arguments, including the gh ``args``.

        Returns:
            object: A completed process carrying the file content.
        """
        fetches.append(str(kwargs.get("args")))
        return _completed(stdout="x = 1\n")

    mock_run_git.return_value = _completed(returncode=1)
    mock_run_gh.side_effect = _serve

    read = _head_reader(context=_pr_context())
    first = read("src/app.py")
    second = read("src/app.py")

    # The second read is served from the memo: same content, one fetch.
    assert_that(first).is_equal_to("x = 1\n")
    assert_that(second).is_equal_to(first)
    assert_that(fetches).is_length(1)


@patch("lintro.ai.review.context.collection._run_gh")
@patch("lintro.ai.review.context.collection._run_git")
def test_pr_mode_validation_drops_a_hunk_that_moved_out_of_the_file(
    mock_run_git: MagicMock,
    mock_run_gh: MagicMock,
) -> None:
    """A gh-fetched head that no longer holds the block drops the suggestion.

    Args:
        mock_run_git: Patched git runner reporting a missing object.
        mock_run_gh: Patched gh runner serving the rewritten file.
    """
    mock_run_git.return_value = _completed(returncode=1)
    mock_run_gh.return_value = _completed(stdout="x = 1\ny = 2\n")

    validated = validate_suggested_patches(
        findings=(
            _finding(
                suggestion_dropped=None,
                suggested_change=SuggestedChange(
                    start_line=3,
                    end_line=3,
                    replacement="z = 4",
                    before="z = 3",
                ),
            ),
        ),
        read_head_file=_head_reader(context=_pr_context()),
    )

    assert_that(validated[0].suggestion_dropped).is_equal_to(
        SuggestionDropReason.STALE_ANCHOR,
    )


def test_sticky_row_states_the_drop_count_and_reasons() -> None:
    """The sticky comment names how many suggestions were withheld and why."""
    row = _suggestion_drops_row(
        result=_result(
            _finding(),
            _finding(suggestion_dropped=SuggestionDropReason.AMBIGUOUS_ANCHOR),
        ),
    )

    assert_that(row).contains("2 suggestions dropped as unsafe to commit")
    assert_that(row).contains("stale_anchor 1")
    assert_that(row).contains("ambiguous_anchor 1")


def test_sticky_row_is_empty_when_every_suggestion_validated() -> None:
    """A clean run adds no drop row to the sticky comment."""
    assert_that(
        _suggestion_drops_row(result=_result(_finding(suggestion_dropped=None))),
    ).is_empty()


def test_terminal_output_marks_dropped_suggestions() -> None:
    """Terminal findings carry the drop reason and a run total."""
    console = Console(width=100, force_terminal=False, record=True)

    render_review_terminal(result=_result(_finding()), console=console)
    text = console.export_text()

    assert_that(text).contains("1 suggestion dropped as unsafe to commit")
    assert_that(text).contains("Suggestion dropped")
    assert_that(text).contains("stale_anchor")


@patch("lintro.ai.review.context.collection._run_gh")
@patch("lintro.ai.review.context.collection._run_git")
def test_cli_helper_strips_a_stale_suggestion_before_post(
    mock_run_git: MagicMock,
    mock_run_gh: MagicMock,
) -> None:
    """The shared pre-post pass clears unvalidated hunks.

    Both the review command and the MCP toolkit render from the result this
    helper returns, so a suggestion that failed validation is gone before any
    GitHub or MCP payload is built.

    Args:
        mock_run_git: Patched git runner reporting a missing object.
        mock_run_gh: Patched gh runner serving the rewritten file.
    """
    from lintro.ai.review.patch_validation import validate_result_suggested_patches

    mock_run_git.return_value = _completed(returncode=1)
    mock_run_gh.return_value = _completed(stdout="x = 1\n")
    finding = _finding(
        suggestion_dropped=None,
        suggested_code="z = 4",
        line=42,
    )

    validated = validate_result_suggested_patches(
        result=_result(finding),
        context=_pr_context(),
    )

    posted = finding_suggested_change(finding=validated.findings[0])
    assert_that(posted).is_none()
    assert_that(validated.findings[0].suggestion_dropped).is_equal_to(
        SuggestionDropReason.STALE_ANCHOR,
    )

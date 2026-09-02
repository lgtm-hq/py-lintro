"""Tests for suggested-patch validation against head file contents (#2101)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from assertpy import assert_that

from lintro.ai.review.enums.suggestion_drop_reason import SuggestionDropReason
from lintro.ai.review.models.review_finding import ReviewFinding, Severity
from lintro.ai.review.models.suggested_change import SuggestedChange
from lintro.ai.review.patch_validation import (
    count_dropped_suggestions,
    describe_suggestion_drops,
    drop_reason_counts,
    dropped_suggestion_findings,
    resolve_anchor,
    validate_suggested_patches,
)

_FILE = "\n".join(
    [
        "def alpha() -> int:",
        "    return 1",
        "",
        "",
        "def beta() -> int:",
        "    value = compute()",
        "    return value",
        "",
    ],
)


def _finding(**overrides: Any) -> ReviewFinding:
    """Build a review finding carrying a suggested change.

    Args:
        **overrides: Fields to override on the base finding.

    Returns:
        The constructed finding.
    """
    base: dict[str, Any] = {
        "severity": Severity.P2,
        "category": "logic-bug",
        "file": "src/app.py",
        "line": 6,
        "title": "Unchecked compute result",
        "description": "d",
        "cause": "c",
        "fix": "f",
        "confidence": "high",
        "suggested_change": SuggestedChange(
            start_line=6,
            end_line=6,
            replacement="    value = compute() or 0",
            before="    value = compute()",
        ),
    }
    base.update(overrides)
    return ReviewFinding(**base)


def _require_change(*, finding: ReviewFinding) -> SuggestedChange:
    """Return a finding's structured change, failing when it has none.

    Args:
        finding: Finding expected to carry a validated change.

    Returns:
        The finding's suggested change.

    Raises:
        AssertionError: When the finding carries no change.
    """
    change = finding.suggested_change
    assert_that(change).is_not_none()
    if change is None:  # pragma: no cover - assertpy already failed the test
        raise AssertionError("finding carries no suggested change")
    return change


def _parsed(payload: dict[str, Any]) -> SuggestedChange:
    """Parse a raw suggested-change payload, failing when it is unusable.

    Args:
        payload: Raw ``suggested_change`` mapping from model output.

    Returns:
        The parsed change.

    Raises:
        AssertionError: When the payload does not parse.
    """
    from lintro.ai.review.models.suggested_change import parse_suggested_change

    change = parse_suggested_change(payload)
    assert_that(change).is_not_none()
    if change is None:  # pragma: no cover - assertpy already failed the test
        raise AssertionError(f"payload did not parse: {payload!r}")
    return change


def _reader(files: dict[str, str]) -> Any:
    """Build a head-file reader backed by an in-memory mapping.

    Args:
        files: Repository-relative path to file content at head.

    Returns:
        A callable returning content for known paths and ``None`` otherwise.
    """

    def _read(path: str) -> str | None:
        """Return the mapped content for a path.

        Args:
            path: Repository-relative path.

        Returns:
            The mapped content, or ``None`` when the path is unknown.
        """
        return files.get(path)

    return _read


def test_exact_anchor_passes_untouched() -> None:
    """A suggestion whose before-block sits on the named lines survives."""
    finding = _finding()

    validated = validate_suggested_patches(
        findings=(finding,),
        read_head_file=_reader({"src/app.py": _FILE}),
    )

    assert_that(validated[0]).is_equal_to(finding)
    assert_that(validated[0].suggestion_dropped).is_none()
    assert_that(count_dropped_suggestions(findings=validated)).is_equal_to(0)


def test_drifted_but_unique_block_is_reanchored() -> None:
    """Drifted line numbers move to the block's single real position."""
    finding = _finding(
        line=2,
        suggested_change=SuggestedChange(
            start_line=2,
            end_line=2,
            replacement="    value = compute() or 0",
            before="    value = compute()",
        ),
    )

    validated = validate_suggested_patches(
        findings=(finding,),
        read_head_file=_reader({"src/app.py": _FILE}),
    )

    assert_that(validated[0].suggestion_dropped).is_none()
    change = _require_change(finding=validated[0])
    assert_that(change.start_line).is_equal_to(6)
    assert_that(change.end_line).is_equal_to(6)
    assert_that(validated[0].line).is_equal_to(6)
    # Surfaces that only read suggested_code (MCP) still see the patch.
    assert_that(validated[0].suggested_code).is_equal_to(change.replacement)


@pytest.mark.parametrize(
    "path",
    ["/etc/passwd", "../outside.py", "src/../../outside.py", "..\\outside.py"],
)
def test_paths_escaping_the_repository_drop_without_reading(path: str) -> None:
    """An absolute or parent-hopping finding path never reaches the reader.

    Args:
        path: Model-authored path that would leave the repository.
    """
    seen: list[str] = []

    def _read(requested: str) -> str | None:
        """Record the request and serve a file that would otherwise pass.

        Args:
            requested: Path the validator asked for.

        Returns:
            Content matching the finding's before block.
        """
        seen.append(requested)
        return _FILE

    validated = validate_suggested_patches(
        findings=(_finding(file=path),),
        read_head_file=_read,
    )

    assert_that(seen).is_empty()
    assert_that(validated[0].suggestion_dropped).is_equal_to(
        SuggestionDropReason.FILE_MISSING,
    )
    assert_that(validated[0].suggested_change).is_none()


def test_before_block_parsed_from_a_payload_drives_reanchoring() -> None:
    """Schema, parser, and validator agree on the ``before`` field.

    The field is declared in the CLI schema and prompt templates, parsed by
    ``parse_suggested_change``, and matched here; this walks one payload
    through parse and validation so the three cannot drift apart unnoticed.
    """
    change = _parsed(
        {
            "lines": [2, 2],
            "replacement": "    value = compute() or 0",
            "before": "    value = compute()",
        },
    )

    validated = validate_suggested_patches(
        findings=(_finding(line=2, suggested_change=change),),
        read_head_file=_reader({"src/app.py": _FILE}),
    )

    assert_that(validated[0].suggestion_dropped).is_none()
    assert_that(_require_change(finding=validated[0]).start_line).is_equal_to(6)


def test_multiline_block_reanchor_keeps_the_span_length() -> None:
    """A re-anchored multi-line hunk covers exactly the matched block."""
    finding = _finding(
        line=20,
        suggested_change=SuggestedChange(
            start_line=20,
            end_line=21,
            replacement="    value = compute() or 0\n    return value",
            before="    value = compute()\n    return value",
        ),
    )

    validated = validate_suggested_patches(
        findings=(finding,),
        read_head_file=_reader({"src/app.py": _FILE}),
    )

    change = _require_change(finding=validated[0])
    assert_that(change.start_line).is_equal_to(6)
    assert_that(change.end_line).is_equal_to(7)
    assert_that(validated[0].line).is_equal_to(6)


def test_missing_block_drops_with_stale_anchor() -> None:
    """A before-block that occurs nowhere at head drops the suggestion."""
    finding = _finding(
        suggested_change=SuggestedChange(
            start_line=6,
            end_line=6,
            replacement="    value = compute() or 0",
            before="    value = long_gone_helper()",
        ),
    )

    validated = validate_suggested_patches(
        findings=(finding,),
        read_head_file=_reader({"src/app.py": _FILE}),
    )

    assert_that(validated[0].suggestion_dropped).is_equal_to(
        SuggestionDropReason.STALE_ANCHOR,
    )
    assert_that(validated[0].suggested_change).is_none()
    assert_that(validated[0].suggested_code).is_empty()
    assert_that(validated[0].description).is_equal_to("d")
    assert_that(validated[0].fix).is_equal_to("f")


def test_ambiguous_block_drops_rather_than_guessing() -> None:
    """A before-block occurring twice at head is never re-anchored."""
    content = "\n".join(["    return value", "x = 1", "    return value", ""])
    finding = _finding(
        line=9,
        suggested_change=SuggestedChange(
            start_line=9,
            end_line=9,
            replacement="    return value or 0",
            before="    return value",
        ),
    )

    validated = validate_suggested_patches(
        findings=(finding,),
        read_head_file=_reader({"src/app.py": content}),
    )

    assert_that(validated[0].suggestion_dropped).is_equal_to(
        SuggestionDropReason.AMBIGUOUS_ANCHOR,
    )
    assert_that(validated[0].suggested_change).is_none()


def test_unreadable_file_drops_with_file_missing() -> None:
    """A finding on a file unreadable at head loses its suggestion."""
    validated = validate_suggested_patches(
        findings=(_finding(),),
        read_head_file=_reader({}),
    )

    assert_that(validated[0].suggestion_dropped).is_equal_to(
        SuggestionDropReason.FILE_MISSING,
    )


def test_empty_path_drops_with_file_missing() -> None:
    """A finding with no usable path cannot be validated, so it drops."""
    validated = validate_suggested_patches(
        findings=(_finding(file="  "),),
        read_head_file=_reader({"src/app.py": _FILE}),
    )

    assert_that(validated[0].suggestion_dropped).is_equal_to(
        SuggestionDropReason.FILE_MISSING,
    )


def test_findings_without_a_suggestion_are_untouched() -> None:
    """A finding proposing no replacement never reads the file at head."""
    finding = _finding(suggested_change=None)

    def _explode(path: str) -> str | None:
        """Fail the test if head content is read.

        Args:
            path: Path that should never be requested.

        Returns:
            Never returns.

        Raises:
            AssertionError: Always.
        """
        raise AssertionError(f"unexpected head read for {path}")

    validated = validate_suggested_patches(
        findings=(finding,),
        read_head_file=_explode,
    )

    assert_that(validated[0]).is_equal_to(finding)


def test_legacy_suggested_code_out_of_range_drops() -> None:
    """An unranged suggested_code on a nonexistent line is not committable."""
    finding = _finding(suggested_change=None, suggested_code="x = 2", line=99)

    validated = validate_suggested_patches(
        findings=(finding,),
        read_head_file=_reader({"src/app.py": _FILE}),
    )

    assert_that(validated[0].suggestion_dropped).is_equal_to(
        SuggestionDropReason.STALE_ANCHOR,
    )
    assert_that(validated[0].suggested_code).is_empty()


def test_legacy_suggested_code_in_range_survives() -> None:
    """An unranged suggested_code on a real line keeps its one-click fix."""
    finding = _finding(suggested_change=None, suggested_code="x = 2", line=2)

    validated = validate_suggested_patches(
        findings=(finding,),
        read_head_file=_reader({"src/app.py": _FILE}),
    )

    assert_that(validated[0].suggestion_dropped).is_none()
    assert_that(validated[0].suggested_code).is_equal_to("x = 2")


def test_change_without_a_before_block_only_checks_the_range() -> None:
    """Without an anchor block, only the line range's existence is verified."""
    inside = SuggestedChange(start_line=2, end_line=2, replacement="    return 2")
    outside = SuggestedChange(start_line=80, end_line=81, replacement="nope")

    assert_that(resolve_anchor(content=_FILE, change=inside).change).is_equal_to(inside)
    assert_that(resolve_anchor(content=_FILE, change=outside).reason).is_equal_to(
        SuggestionDropReason.STALE_ANCHOR,
    )


@pytest.mark.parametrize(
    ("start_line", "end_line"),
    [(0, 1), (3, 2), (1, 99)],
    ids=["case=zero_start", "case=inverted", "case=past_eof"],
)
def test_out_of_range_spans_without_a_block_drop(
    *,
    start_line: int,
    end_line: int,
) -> None:
    """A range that cannot exist at head is a stale anchor.

    Args:
        start_line: First line of the proposed span.
        end_line: Last line of the proposed span.
    """
    change = SuggestedChange(
        start_line=start_line,
        end_line=end_line,
        replacement="x",
    )

    assert_that(resolve_anchor(content=_FILE, change=change).reason).is_equal_to(
        SuggestionDropReason.STALE_ANCHOR,
    )


def test_drop_summaries_report_counts_and_reasons() -> None:
    """The run summary names every drop and its reason."""
    stale = _finding(
        suggested_change=SuggestedChange(
            start_line=6,
            end_line=6,
            replacement="r",
            before="    nothing like this",
        ),
    )
    missing = _finding(file="src/gone.py")

    validated = validate_suggested_patches(
        findings=(stale, missing, _finding()),
        read_head_file=_reader({"src/app.py": _FILE}),
    )

    assert_that(count_dropped_suggestions(findings=validated)).is_equal_to(2)
    assert_that(dropped_suggestion_findings(findings=validated)).is_length(2)
    assert_that(drop_reason_counts(findings=validated)).is_equal_to(
        {"stale_anchor": 1, "file_missing": 1},
    )
    notice = describe_suggestion_drops(findings=validated)
    assert_that(notice).starts_with("2 suggestions dropped as unsafe to commit")
    assert_that(notice).contains("stale_anchor 1")
    assert_that(notice).contains("file_missing 1")


def test_no_drops_produces_no_notice() -> None:
    """A clean run renders no drop notice at all."""
    validated = validate_suggested_patches(
        findings=(_finding(),),
        read_head_file=_reader({"src/app.py": _FILE}),
    )

    assert_that(describe_suggestion_drops(findings=validated)).is_empty()
    assert_that(drop_reason_counts(findings=validated)).is_empty()


def test_suggested_change_before_round_trips_through_the_payload() -> None:
    """The before block survives model payload to model and back."""
    change = _parsed({"lines": [6, 6], "replacement": "r", "before": "b"})

    assert_that(change.before).is_equal_to("b")
    assert_that(change.to_dict()).is_equal_to(
        {"lines": [6, 6], "replacement": "r", "before": "b"},
    )


def test_suggested_change_without_before_omits_the_key() -> None:
    """A payload predating #2101 round-trips without gaining a key."""
    change = _parsed({"lines": [6, 6], "replacement": "r"})

    assert_that(change.before).is_empty()
    assert_that(change.to_dict()).does_not_contain_key("before")


def test_drop_count_is_visible_in_json_output() -> None:
    """JSON output carries the run's drop total, tally, and per-finding tag."""
    from lintro.ai.review.models.review_metadata import ReviewMetadata
    from lintro.ai.review.models.review_result import ReviewResult
    from lintro.ai.review.output import render_review_json

    validated = validate_suggested_patches(
        findings=(_finding(file="src/gone.py"),),
        read_head_file=_reader({}),
    )
    result = ReviewResult(
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
        findings=validated,
    )

    payload = json.loads(render_review_json(result=result))

    assert_that(payload["suggestions_dropped"]).is_equal_to(1)
    assert_that(payload["suggestions_dropped_by_reason"]).is_equal_to(
        {"file_missing": 1},
    )
    assert_that(payload["findings"][0]["suggestion_dropped"]).is_equal_to(
        "file_missing",
    )

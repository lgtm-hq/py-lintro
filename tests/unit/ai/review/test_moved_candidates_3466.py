"""Which prior findings need the moved-finding check, and how answers parse (#3466)."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from assertpy import assert_that

from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.enums.moved_verdict import MovedVerdict
from lintro.ai.review.models.delta_plan import (
    LOCATION_BASE_MOVED,
    LOCATION_UNCHECKED,
    LOCATION_UNMAPPED,
)
from lintro.ai.review.models.finding_record import FindingRecord
from lintro.ai.review.models.review_finding import ReviewFinding, Severity
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.moved_candidates import (
    moved_verdict,
    needs_verification,
    placements,
    select_candidates,
)
from lintro.ai.review.moved_verification_prompt import parse_moved_answer

_HEAD = "a" * 40
_NEXT = "b" * 40


def _record(*, file: str = "a.py", origin_path: str = "") -> FindingRecord:
    """An open X recorded at ``file:81``.

    Args:
        file: The stored path.
        origin_path: The path it was first tracked at, after a move.

    Returns:
        The record.
    """
    return FindingRecord(
        fingerprint="f" * 16,
        ordinal=1,
        severity=Severity.P2,
        category="logic-bug",
        file=file,
        line=81,
        title="X",
        status=FindingStatus.OPEN,
        line_sha=_HEAD,
        origin_path=origin_path,
    )


@pytest.mark.parametrize(
    ("relocation", "departed", "origin", "expected"),
    [
        pytest.param(None, None, "", False, id="untouched"),
        pytest.param(("a.py", 90, _NEXT), None, "", False, id="line-mapped-in-place"),
        pytest.param(("b.py", 81, _NEXT), None, "", True, id="rename-followed"),
        pytest.param(("b.py", 81, ""), None, "", True, id="weak-rename"),
        pytest.param(("a.py", 81, LOCATION_UNMAPPED), None, "", True, id="unmapped"),
        pytest.param(
            ("a.py", 81, LOCATION_BASE_MOVED),
            None,
            "",
            True,
            id="base-moved",
        ),
        pytest.param(None, {"a.py"}, "", True, id="deleted"),
        pytest.param(None, None, "z.py", True, id="lineage"),
        pytest.param(
            ("a.py", 81, LOCATION_UNCHECKED),
            {"a.py"},
            "z.py",
            False,
            id="beyond-the-mapping-cap",
        ),
    ],
)
def test_only_an_in_place_record_skips_the_check(
    relocation: tuple[str, int, str] | None,
    departed: set[str] | None,
    origin: str,
    expected: bool,
) -> None:
    """Every inferred placement needs the check; an exact in-place one does not.

    Args:
        relocation: Where the round placed the record.
        departed: Paths that left the diff.
        origin: The record's lineage.
        expected: Whether the record needs the check.
    """
    assert_that(
        needs_verification(
            record=_record(origin_path=origin),
            relocation=relocation,
            departed_paths=departed,
        ),
    ).is_equal_to(expected)


def test_a_candidate_without_a_verdict_is_unknown_and_one_in_place_has_none() -> None:
    """Silence is never a verdict: a moved record with no answer is held."""
    record = _record()
    moved = {record.key: ("b.py", 81, _NEXT)}

    assert_that(
        moved_verdict(record=record, relocations=moved, departed_paths=None),
    ).is_equal_to(MovedVerdict.UNKNOWN)
    assert_that(
        moved_verdict(record=record, relocations=None, departed_paths=None),
    ).is_none()
    answered = placements(
        relocations=moved,
        verdicts=((record.key, "fixed", "", 0, ""),),
    )
    assert_that(
        moved_verdict(record=record, relocations=answered, departed_paths=None),
    ).is_equal_to(MovedVerdict.FIXED)


def test_a_record_a_report_pairs_with_is_not_asked_about() -> None:
    """The renamed X reported again on ``b.py`` is matched, not a candidate."""
    record = _record()
    other = replace(_record(file="c.py"), fingerprint="e" * 16)
    state = ReviewState(findings=(record, other))
    reported = [
        ReviewFinding(
            severity=Severity.P2,
            category="logic-bug",
            file="b.py",
            line=81,
            title="X",
            description="d",
            cause="c",
            fix="f",
            confidence="high",
        ),
    ]

    chosen = select_candidates(
        prior=state,
        relocations={
            record.key: ("b.py", 81, _NEXT),
            other.key: ("d.py", 81, _NEXT),
        },
        departed_paths=None,
        findings=reported,
    )

    assert_that([item.key for item in chosen]).is_equal_to([other.key])


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        pytest.param("not json", None, id="not-json"),
        pytest.param('{"verifications": []}', None, id="another-schema"),
        pytest.param(
            json.dumps({"moved_findings": [{"index": 1, "still_present": False}]}),
            {1: (MovedVerdict.FIXED, "", 0)},
            id="json-boolean",
        ),
        pytest.param(
            json.dumps(
                {
                    "moved_findings": [
                        {
                            "index": 1,
                            "still_present": "true",
                            "file": "./b.py",
                            "line": 7,
                        },
                        {"index": 2, "still_present": "maybe"},
                        {"index": 9, "still_present": "false"},
                        {"index": True, "still_present": "false"},
                    ],
                },
            ),
            {1: (MovedVerdict.PRESENT, "b.py", 7)},
            id="bad-entries-skipped",
        ),
        pytest.param(
            json.dumps(
                {
                    "moved_findings": [
                        {"index": 1, "still_present": "false"},
                        {"index": 1, "still_present": "true"},
                    ],
                },
            ),
            {1: (MovedVerdict.UNKNOWN, "", 0)},
            id="conflicting-answers",
        ),
    ],
)
def test_the_answer_parses_per_finding(
    content: str,
    expected: dict[int, tuple[MovedVerdict, str, int]] | None,
) -> None:
    """A malformed entry costs its finding a verdict, never another's.

    Args:
        content: The provider's answer.
        expected: The parsed verdicts.
    """
    assert_that(parse_moved_answer(content=content, count=2)).is_equal_to(expected)

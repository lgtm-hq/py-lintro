"""A saved lint report never makes a tool look finished on bad evidence (#3497).

A ``--lint-report`` is untrusted. A malformed verdict rejects the report, as
other malformed shapes already do, and the review runs from the diff alone
with a header note. Well-typed but contradictory evidence makes the entry
incomplete, so its warning reaches the prompt. Every case runs the real
load, restrict and format path through ``build_lint_digest_from_report``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that

from lintro.ai.review.lint_completeness import LintRunCounts
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.preparation_resolvers import (
    LINT_FACTS_UNAVAILABLE,
    build_lint_digest_from_report,
)

_CHANGED = "src/app.py"

#: A finding on the review's changed file.
_FINDING: dict[str, Any] = {
    "file": _CHANGED,
    "line": 3,
    "code": "X1",
    "message": "kept finding",
}


def _digest(
    tmp_path: Path,
    entry: dict[str, Any],
) -> tuple[str | None, LintRunCounts, str]:
    """Write a one-entry report and digest it as ``--lint-report`` does.

    Args:
        tmp_path: Pytest temporary directory.
        entry: The report's only per-tool entry.

    Returns:
        The digest, the counts and the header note.
    """
    return _digest_text(tmp_path, json.dumps({"results": [entry]}))


def _digest_text(
    tmp_path: Path,
    text: str,
) -> tuple[str | None, LintRunCounts, str]:
    """Write *text* as the report and digest it as ``--lint-report`` does.

    Args:
        tmp_path: Pytest temporary directory.
        text: The report file's raw contents.

    Returns:
        The digest, the counts and the header note.
    """
    report = tmp_path / "results.json"
    report.write_text(text, encoding="utf-8")
    context = ReviewContext(
        base_ref="main",
        head_ref="abc123",
        changed_files=[
            ChangedFile(path=_CHANGED, status="modified", additions=1, deletions=0),
        ],
        unified_diff="",
    )
    return build_lint_digest_from_report(context=context, report_path=report)


@pytest.mark.parametrize(
    ("entry", "problem"),
    [
        pytest.param(
            {"tool": "tsc", "success": "false", "issues": []},
            "has a non-bool 'success'",
            id="string-success",
        ),
        pytest.param({"tool": "tsc"}, "has no 'success' verdict", id="no-success"),
        pytest.param(
            {"tool": "tsc", "success": None},
            "has a non-bool 'success'",
            id="null-success",
        ),
        pytest.param(
            {"tool": "tsc", "success": True, "status": "bogus"},
            "has an unknown 'status' 'bogus'",
            id="unknown-status",
        ),
        pytest.param(
            {"tool": "tsc", "success": True, "status": 3},
            "has a non-str 'status'",
            id="non-string-status",
        ),
        pytest.param(
            {"tool": "tsc", "success": True, "status": "timed_out", "timed_out": None},
            "has a non-bool 'timed_out'",
            id="null-timed-out",
        ),
        pytest.param(
            {"tool": "tsc", "success": True, "execution_failed": "yes"},
            "has a non-bool 'execution_failed'",
            id="string-execution-failed",
        ),
        pytest.param(
            {"tool": "tsc", "success": True, "issues": None},
            "'issues' is not a list",
            id="null-issues",
        ),
        pytest.param(
            {"tool": "tsc", "success": True, "issues_count": "1"},
            "has a non-int 'issues_count'",
            id="string-issues-count",
        ),
        pytest.param(
            {"tool": "tsc", "success": True, "issues_count": 3, "issues": []},
            "has 'issues_count' 3 but lists 0 issue(s)",
            id="issues-count-mismatch",
        ),
        pytest.param(
            {"tool": "tsc", "success": True, "issues_count": None},
            "has a non-int 'issues_count'",
            id="null-issues-count",
        ),
        pytest.param(
            {"tool": "ruff", "success": True, "remaining": -1},
            "carries partial fix accounting",
            id="negative-remaining-alone",
        ),
    ],
)
def test_malformed_verdict_rejects_the_report(
    tmp_path: Path,
    entry: dict[str, Any],
    problem: str,
) -> None:
    """A missing or mistyped verdict is never read as a finished tool.

    Args:
        tmp_path: Pytest temporary directory.
        entry: A malformed report entry.
        problem: What the header note must name.
    """
    digest, counts, note = _digest(tmp_path, entry)

    assert_that(digest).is_none()
    assert_that((counts.ran, counts.incomplete)).is_equal_to((0, 0))
    assert_that(note).starts_with(f"{LINT_FACTS_UNAVAILABLE}: lint report results[0]")
    assert_that(note).contains(problem)


@pytest.mark.parametrize(
    ("entry", "note"),
    [
        pytest.param(
            {
                "tool": "tsc",
                "status": "errored",
                "success": False,
                "skipped": True,
                "skip_reason": "done",
            },
            "report entry for tsc claims to be skipped but carries a failure or "
            "findings",
            id="errored-status-beside-skip",
        ),
        pytest.param(
            {"tool": "tsc", "status": "timed_out", "success": True},
            "timed out",
            id="timed-out-status-with-success",
        ),
        pytest.param(
            {"tool": "tsc", "status": "ok", "success": False},
            "report entry for tsc has status 'ok' but reports failure or findings",
            id="clean-status-beside-failure",
        ),
        pytest.param(
            {"tool": "tsc", "status": "failed", "success": True},
            "report entry for tsc has status 'failed' but reports success and no "
            "findings",
            id="failed-status-beside-success",
        ),
        pytest.param(
            {"tool": "tsc", "status": "skipped", "success": True},
            "report entry for tsc claims to be skipped without consistent status "
            "evidence",
            id="skip-status-without-skip-flag",
        ),
        pytest.param(
            {
                "tool": "tsc",
                "status": "failed",
                "success": False,
                "unavailable": True,
                "unavailable_reason": "missing",
            },
            "report entry for tsc claims to be unavailable without consistent "
            "status evidence",
            id="unavailable-beside-failed-status",
        ),
        pytest.param(
            {"tool": "tsc", "status": "errored", "success": True, "issues": [_FINDING]},
            "report entry for tsc has status 'errored'",
            id="errored-status-with-a-finding",
        ),
    ],
)
def test_contradictory_status_evidence_is_incomplete(
    tmp_path: Path,
    entry: dict[str, Any],
    note: str,
) -> None:
    """Incomplete evidence in ``status`` or the flags wins over any clean claim.

    Args:
        tmp_path: Pytest temporary directory.
        entry: A well-typed entry whose evidence disagrees.
        note: The reason the digest must give.
    """
    digest, counts, header = _digest(tmp_path, entry)

    assert_that(header).is_empty()
    assert_that((counts.ran, counts.incomplete)).is_equal_to((0, 1))
    assert_that(digest).contains(f"Tool: tsc | did not run / incomplete: {note}")
    assert_that(digest is not None and "kept finding" in digest).is_equal_to(
        bool(entry.get("issues")),
    )


def test_null_residual_reason_is_not_repeated(tmp_path: Path) -> None:
    """A residual with no reason reads once, without a parenthesised echo.

    Args:
        tmp_path: Pytest temporary directory.
    """
    digest, _counts, _note = _digest(
        tmp_path,
        {
            "tool": "mypy",
            "success": False,
            "residual_unknown": True,
            "residual_unknown_reason": None,
        },
    )

    assert_that(digest).contains(
        "Tool: mypy | did not run / incomplete: remaining issues unknown\n",
    )
    assert_that(digest).does_not_contain("(remaining issues unknown)")


def _assert_rejected_as_invalid_json(
    result: tuple[str | None, LintRunCounts, str],
) -> None:
    """Assert a digest call rejected the report with the invalid-JSON note.

    Args:
        result: What ``build_lint_digest_from_report`` returned.
    """
    digest, counts, note = result
    assert_that(digest).is_none()
    assert_that((counts.ran, counts.incomplete)).is_equal_to((0, 0))
    assert_that(note).starts_with(
        f"{LINT_FACTS_UNAVAILABLE}: lint report is not valid JSON",
    )


def test_integer_past_the_digit_limit_is_a_header_note(tmp_path: Path) -> None:
    """A valid report with a 5,000-digit integer is rejected, never raised.

    Args:
        tmp_path: Pytest temporary directory.
    """
    text = (
        '{"results": [{"tool": "tsc", "success": true}], "extra": ' + "9" * 5000 + "}"
    )

    _assert_rejected_as_invalid_json(_digest_text(tmp_path, text))


def test_nesting_too_deep_to_decode_is_a_header_note(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``RecursionError`` from the decoder is a rejected report.

    How deep a document must be to exhaust the decoder depends on the
    interpreter and its C stack (Python 3.14 decodes 100,000 levels on some
    hosts and not on others), so the decoder is made to raise directly.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest patch fixture.
    """

    def _too_deep(*_args: object, **_kwargs: object) -> object:
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(json, "loads", _too_deep)

    _assert_rejected_as_invalid_json(_digest_text(tmp_path, '{"results": []}'))

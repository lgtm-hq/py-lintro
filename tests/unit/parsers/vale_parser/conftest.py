"""Shared fixtures and helpers for Vale parser tests."""

from __future__ import annotations

import json

import pytest

# Real ``vale --output=JSON`` output captured from Vale 3.15.1 running the
# built-in ``Vale`` style over a Markdown fixture with repeated words and a
# spelling issue. Used to validate the parser against genuine tool output.
REAL_VALE_OUTPUT: str = json.dumps(
    {
        "vale_violations.md": [
            {
                "Action": {"Name": "edit", "Params": ["truncate", " "]},
                "Span": [1, 7],
                "Check": "Vale.Repetition",
                "Description": "",
                "Link": "",
                "Message": "'the' is repeated!",
                "Severity": "error",
                "Match": "The the",
                "Line": 3,
            },
            {
                "Action": {"Name": "suggest", "Params": ["spellings"]},
                "Span": [37, 46],
                "Check": "Vale.Spelling",
                "Description": "",
                "Link": "",
                "Message": "Did you really mean 'performant'?",
                "Severity": "error",
                "Match": "performant",
                "Line": 3,
            },
            {
                "Action": {"Name": "edit", "Params": ["truncate", " "]},
                "Span": [44, 54],
                "Check": "Vale.Repetition",
                "Description": "",
                "Link": "",
                "Message": "'words' is repeated!",
                "Severity": "error",
                "Match": "words words",
                "Line": 5,
            },
        ],
    },
)


def make_alert(
    *,
    check: str = "Vale.Repetition",
    message: str = "issue",
    severity: str = "error",
    line: int = 1,
    span: list[int] | None = None,
    link: str = "",
    match: str = "",
) -> dict[str, object]:
    """Build a single Vale alert dict for synthetic parser fixtures.

    Args:
        check: The Vale check name (``<Style>.<Rule>``).
        message: Human-readable alert message.
        severity: Native Vale severity (``error``/``warning``/``suggestion``).
        line: 1-based line number of the alert.
        span: Optional ``[start, end]`` column span; column is ``span[0]``.
        link: Optional documentation URL for the check.
        match: Optional matched source text.

    Returns:
        A dict shaped like a single entry in Vale's per-file alert list.
    """
    return {
        "Span": span if span is not None else [1, 5],
        "Check": check,
        "Description": "",
        "Link": link,
        "Message": message,
        "Severity": severity,
        "Match": match,
        "Line": line,
    }


def make_output(alerts_by_file: dict[str, list[dict[str, object]]]) -> str:
    """Serialize a mapping of file path to alert list as Vale JSON.

    Args:
        alerts_by_file: Mapping of file path to a list of alert dicts.

    Returns:
        A JSON string in Vale's ``--output=JSON`` shape.
    """
    return json.dumps(alerts_by_file)


@pytest.fixture
def real_vale_output() -> str:
    """Return real captured Vale JSON output.

    Returns:
        The captured ``vale --output=JSON`` payload as a string.
    """
    return REAL_VALE_OUTPUT

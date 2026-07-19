"""Unit tests for the bandit parser registration (#1534, #1044)."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.utils.output.parser_registration import (
    ParserError,
    _parse_bandit_output,
)


@pytest.mark.parametrize(
    ("informational",),
    [
        ("No .py/.pyi files found to check.",),
        ("No .py/.pyi files found",),
        ("Bandit ran successfully and found no issues",),
    ],
    ids=["no-files-message", "no-files-sentinel", "clean-pass-sentinel"],
)
def test_parse_bandit_output_treats_sentinels_as_no_issues(
    informational: str,
) -> None:
    """Verify informational sentinels parse to an empty issue list, not an error.

    Args:
        informational: A non-JSON informational message from a clean run.
    """
    issues = _parse_bandit_output(informational)

    assert_that(issues).is_instance_of(list)
    assert_that(issues).is_empty()


def test_parse_bandit_output_raises_on_real_unparseable_output() -> None:
    """Verify genuine unparseable bandit output still raises ParserError (#1044)."""
    assert_that(_parse_bandit_output).raises(ParserError).when_called_with(
        "not valid json { broken",
    )


def test_parse_bandit_output_parses_valid_json() -> None:
    """Verify valid bandit JSON is parsed into issue objects."""
    bandit_output = (
        '{"results": [{"filename": "src/main.py", "line_number": 10, '
        '"test_id": "B101", "issue_text": "Security issue", '
        '"issue_severity": "HIGH", "issue_confidence": "HIGH"}]}'
    )

    issues = _parse_bandit_output(bandit_output)

    assert_that(issues).is_length(1)

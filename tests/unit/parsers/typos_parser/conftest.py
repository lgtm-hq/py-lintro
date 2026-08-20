"""Shared fixtures and helpers for typos parser tests.

The sample payloads mirror real ``typos --format json`` output (newline
delimited JSON, one object per finding) captured from typos-cli 1.48.0.
"""

from __future__ import annotations

import json

import pytest


def make_typo_record(
    *,
    path: str = "sample.txt",
    line_num: int = 1,
    byte_offset: int = 0,
    typo: str = "teh",
    corrections: list[str] | None = None,
) -> str:
    """Build a single typos JSON line for a ``typo`` finding.

    Args:
        path: File path reported by typos.
        line_num: 1-based line number.
        byte_offset: 0-based byte offset of the typo within the line.
        typo: The misspelled word.
        corrections: Suggested corrections (defaults to ``["the"]``).

    Returns:
        A JSON-encoded string matching typos' ``--format json`` output.
    """
    return json.dumps(
        {
            "type": "typo",
            "path": path,
            "line_num": line_num,
            "byte_offset": byte_offset,
            "typo": typo,
            "corrections": ["the"] if corrections is None else corrections,
        },
    )


def make_typos_output(records: list[str]) -> str:
    """Join JSON records into newline-delimited typos output.

    Args:
        records: JSON line strings (e.g. from :func:`make_typo_record`).

    Returns:
        Newline-joined output string.
    """
    return "\n".join(records)


@pytest.fixture
def single_typo_output() -> str:
    """Provide a realistic single-finding typos payload.

    Returns:
        Newline-delimited JSON with one typo finding.
    """
    return make_typo_record(
        path="README.md",
        line_num=3,
        byte_offset=18,
        typo="teh",
        corrections=["the"],
    )


@pytest.fixture
def multi_typo_output() -> str:
    """Provide a realistic multi-finding typos payload.

    Returns:
        Newline-delimited JSON with several typo findings.
    """
    return make_typos_output(
        [
            make_typo_record(
                path="a.txt",
                line_num=1,
                byte_offset=19,
                typo="teh",
                corrections=["the"],
            ),
            make_typo_record(
                path="a.txt",
                line_num=2,
                byte_offset=3,
                typo="seperate",
                corrections=["separate"],
            ),
            make_typo_record(
                path="b.py",
                line_num=10,
                byte_offset=0,
                typo="reprot",
                corrections=["report"],
            ),
        ],
    )

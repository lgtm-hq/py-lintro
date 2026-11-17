"""``lintro fmt`` lists every typos correction through the real CLI (#3509)."""

from __future__ import annotations

from pathlib import Path

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli_utils.commands.format import format_command
from tests.integration._tools import require_tool

pytestmark = require_tool("typos")


def test_fmt_lists_corrections_when_a_typo_is_left_unfixed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify the listing survives the convergence retries an unfixable typo causes.

    ``CONNEC`` has two candidate corrections, so typos leaves it and lintro
    retries the fix; the retries write nothing, and before #3509's review the
    CLI showed only their empty listing.

    Args:
        tmp_path: Pytest temporary directory fixture.
        monkeypatch: Pytest monkeypatch fixture used to isolate the cwd.
    """
    monkeypatch.chdir(tmp_path)
    Path("doc.txt").write_text(
        "teh cat teh dog\nCONNEC here and recieve\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(format_command, ["--tools", "typos", "doc.txt"])

    assert_that(Path("doc.txt").read_text(encoding="utf-8")).is_equal_to(
        "the cat the dog\nCONNEC here and receive\n",
    )
    listed = [
        line.strip()
        for line in result.output.splitlines()
        if line.strip().startswith("doc.txt:")
    ]
    assert_that(listed).is_equal_to(
        [
            "doc.txt:1: teh -> the",
            "doc.txt:1: teh -> the",
            "doc.txt:2: recieve -> receive",
        ],
    )

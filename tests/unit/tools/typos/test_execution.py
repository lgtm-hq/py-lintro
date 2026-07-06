"""Tests for typos plugin check and fix execution."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from assertpy import assert_that

from lintro.tools.definitions.typos import TyposPlugin


def _typo_line(path: str, typo: str, correction: str) -> str:
    """Build one JSON line of typos output.

    Args:
        path: Reported file path.
        typo: Misspelled word.
        correction: Suggested correction.

    Returns:
        A JSON-encoded typos finding line.
    """
    return json.dumps(
        {
            "type": "typo",
            "path": path,
            "line_num": 1,
            "byte_offset": 0,
            "typo": typo,
            "corrections": [correction],
        },
    )


def test_check_success_when_clean(
    typos_plugin: TyposPlugin,
    tmp_path: Path,
) -> None:
    """Check returns success and no issues when typos finds nothing."""
    target = tmp_path / "clean.txt"
    target.write_text("all good words here\n")

    with patch.object(typos_plugin, "_run_subprocess", return_value=(True, "")):
        result = typos_plugin.check([str(target)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_reports_issues(
    typos_plugin: TyposPlugin,
    tmp_path: Path,
) -> None:
    """Check surfaces parsed issues and fails when typos are present."""
    target = tmp_path / "bad.txt"
    target.write_text("teh cat\n")
    output = _typo_line("bad.txt", "teh", "the")

    with patch.object(typos_plugin, "_run_subprocess", return_value=(False, output)):
        result = typos_plugin.check([str(target)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)
    assert_that(result.issues[0].typo).is_equal_to("teh")


def test_check_timeout_returns_failure(
    typos_plugin: TyposPlugin,
    tmp_path: Path,
) -> None:
    """Check handles a subprocess timeout gracefully."""
    target = tmp_path / "slow.txt"
    target.write_text("content\n")

    with patch.object(
        typos_plugin,
        "_run_subprocess",
        side_effect=subprocess.TimeoutExpired(cmd=["typos"], timeout=30),
    ):
        result = typos_plugin.check([str(target)], {})

    assert_that(result.success).is_false()
    assert_that(result.output).contains("timed out")


def test_fix_corrects_all_typos(
    typos_plugin: TyposPlugin,
    tmp_path: Path,
) -> None:
    """Fix reports every typo as fixed when the re-check is clean."""
    target = tmp_path / "fixme.txt"
    target.write_text("teh cat\n")
    initial = _typo_line("fixme.txt", "teh", "the")

    # Sequence: initial check, write-changes, re-check (clean).
    with patch.object(
        typos_plugin,
        "_run_subprocess",
        side_effect=[(False, initial), (True, ""), (True, "")],
    ):
        result = typos_plugin.fix([str(target)], {})

    assert_that(result.success).is_true()
    assert_that(result.initial_issues_count).is_equal_to(1)
    assert_that(result.fixed_issues_count).is_equal_to(1)
    assert_that(result.remaining_issues_count).is_equal_to(0)
    # Invariant: initial == fixed + remaining.
    assert_that(result.fixed_issues_count + result.remaining_issues_count).is_equal_to(
        result.initial_issues_count,
    )


def test_fix_partial_leaves_remaining(
    typos_plugin: TyposPlugin,
    tmp_path: Path,
) -> None:
    """Fix reports remaining typos when the re-check still finds one."""
    target = tmp_path / "fixme.txt"
    target.write_text("teh seperate cat\n")
    initial = "\n".join(
        [_typo_line("fixme.txt", "teh", "the"), _typo_line("fixme.txt", "xyz", "abc")],
    )
    remaining = _typo_line("fixme.txt", "xyz", "abc")

    with patch.object(
        typos_plugin,
        "_run_subprocess",
        side_effect=[(False, initial), (True, ""), (False, remaining)],
    ):
        result = typos_plugin.fix([str(target)], {})

    assert_that(result.success).is_false()
    assert_that(result.initial_issues_count).is_equal_to(2)
    assert_that(result.fixed_issues_count).is_equal_to(1)
    assert_that(result.remaining_issues_count).is_equal_to(1)


def test_fix_timeout_returns_failure(
    typos_plugin: TyposPlugin,
    tmp_path: Path,
) -> None:
    """Fix handles a timeout during the initial detection pass."""
    target = tmp_path / "slow.txt"
    target.write_text("teh\n")

    with patch.object(
        typos_plugin,
        "_run_subprocess",
        side_effect=subprocess.TimeoutExpired(cmd=["typos"], timeout=30),
    ):
        result = typos_plugin.fix([str(target)], {})

    assert_that(result.success).is_false()
    assert_that(result.output).contains("timed out")

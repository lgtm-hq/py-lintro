"""Tests that ``typos`` fix output lists every correction it wrote (#3509)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from assertpy import assert_that

from lintro.plugins.subprocess_executor import SubprocessResult
from lintro.tools.typos.definition import TyposPlugin


def _finding(line: int, offset: int, typo: str, corrections: list[str]) -> str:
    """Build one JSON line of ``typos --format json`` output for ``doc.txt``.

    Args:
        line: 1-based line number.
        offset: Byte offset within the line.
        typo: Misspelled word.
        corrections: Suggested replacements.

    Returns:
        A JSON-encoded typos finding.
    """
    return json.dumps(
        {
            "type": "typo",
            "path": "doc.txt",
            "line_num": line,
            "byte_offset": offset,
            "typo": typo,
            "corrections": corrections,
        },
    )


def _found(*lines: str) -> SubprocessResult:
    """Build the result of a typos run that reported findings.

    Args:
        *lines: JSON finding lines.

    Returns:
        A SubprocessResult with exit code 2 and the findings on stdout.
    """
    stdout = "\n".join(lines) + "\n"
    return SubprocessResult(returncode=2, stdout=stdout, stderr="", output=stdout)


_CLEAN = SubprocessResult(returncode=0, stdout="", stderr="", output="")


def _fix_output(
    plugin: TyposPlugin,
    tmp_path: Path,
    detect: SubprocessResult,
    recheck: SubprocessResult,
) -> str:
    """Run ``fix`` over canned detect / write / re-check passes.

    Args:
        plugin: Plugin under test.
        tmp_path: Directory holding the target file.
        detect: Result of the pre-fix scan.
        recheck: Result of the post-fix re-check.

    Returns:
        The fix result's display output.
    """
    target = tmp_path / "doc.txt"
    target.write_text("placeholder\n")
    with patch.object(
        plugin,
        "_run_subprocess_result",
        side_effect=[detect, _CLEAN, recheck],
    ):
        result = plugin.fix([str(target)], {})
    return result.output or ""


def test_fix_output_lists_each_applied_correction(
    typos_plugin: TyposPlugin,
    tmp_path: Path,
) -> None:
    """Every rewrite appears as ``file:line: old -> new`` under the summary.

    Args:
        typos_plugin: Plugin fixture with version checking mocked out.
        tmp_path: Pytest temporary directory fixture.
    """
    detect = _found(
        _finding(1, 0, "teh", ["the"]),
        _finding(3, 26, "DELET", ["DELETE"]),
    )

    output = _fix_output(typos_plugin, tmp_path, detect=detect, recheck=_CLEAN)

    assert_that(output.splitlines()).is_equal_to(
        [
            "Fixed 2 typo(s).",
            "  doc.txt:1: teh -> the",
            "  doc.txt:3: DELET -> DELETE",
        ],
    )


def test_fix_output_omits_typos_the_recheck_still_reports(
    typos_plugin: TyposPlugin,
    tmp_path: Path,
) -> None:
    """A word still present after the write is not listed as changed.

    typos leaves ambiguous words alone, and a second occurrence on the same
    line keeps its word even though its byte offset moved.

    Args:
        typos_plugin: Plugin fixture with version checking mocked out.
        tmp_path: Pytest temporary directory fixture.
    """
    detect = _found(
        _finding(1, 0, "teh", ["the"]),
        _finding(1, 8, "teh", ["the"]),
        _finding(2, 0, "CONNEC", ["CONNECT", "CONNED"]),
    )
    recheck = _found(
        _finding(1, 8, "teh", ["the"]),
        _finding(2, 0, "CONNEC", ["CONNECT", "CONNED"]),
    )

    output = _fix_output(typos_plugin, tmp_path, detect=detect, recheck=recheck)

    assert_that(output.splitlines()).is_equal_to(
        [
            "Fixed 1 typo(s); 2 could not be auto-corrected.",
            "  doc.txt:1: teh -> the",
        ],
    )

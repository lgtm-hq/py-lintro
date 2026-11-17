"""Tests that ``typos`` fix output lists every correction it wrote (#3509)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from assertpy import assert_that

from lintro.plugins.subprocess_executor import SubprocessResult
from lintro.tools.typos.definition import TyposPlugin
from lintro.utils.execution.fix_retry import APPLIED_CHANGES_KEY, run_fix_with_retry


def _finding(
    line: int,
    offset: int,
    typo: str,
    corrections: list[str],
    path: str = "doc.txt",
) -> str:
    """Build one JSON line of ``typos --format json`` output.

    Args:
        line: 1-based line number.
        offset: Byte offset within the line.
        typo: Misspelled word.
        corrections: Suggested replacements.
        path: Reported file path.

    Returns:
        A JSON-encoded typos finding.
    """
    return json.dumps(
        {
            "type": "typo",
            "path": path,
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


def _target(tmp_path: Path) -> str:
    """Create the file every canned run is pointed at.

    Args:
        tmp_path: Directory to create it in.

    Returns:
        The file's path.
    """
    target = tmp_path / "doc.txt"
    target.write_text("placeholder\n")
    return str(target)


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
    with patch.object(
        plugin,
        "_run_subprocess_result",
        side_effect=[detect, _CLEAN, recheck],
    ):
        result = plugin.fix([_target(tmp_path)], {})
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


def test_fix_output_drops_line_numbers_when_a_replacement_adds_lines(
    typos_plugin: TyposPlugin,
    tmp_path: Path,
) -> None:
    r"""A multi-line replacement shifts lines, so no line number is claimed.

    With ``foo = "hello\\nworld"`` configured, ``foo`` on line 1 becomes two
    lines and the ambiguous ``CONNEC`` moves from line 2 to line 3 untouched.
    It must not be listed as corrected (review of #3515).

    Args:
        typos_plugin: Plugin fixture with version checking mocked out.
        tmp_path: Pytest temporary directory fixture.
    """
    detect = _found(
        _finding(1, 0, "foo", ["hello\nworld"]),
        _finding(2, 0, "CONNEC", ["CONNECT", "CONNED"]),
    )
    recheck = _found(_finding(3, 0, "CONNEC", ["CONNECT", "CONNED"]))

    output = _fix_output(typos_plugin, tmp_path, detect=detect, recheck=recheck)

    assert_that(output.splitlines()).is_equal_to(
        [
            "Fixed 1 typo(s); 1 could not be auto-corrected.",
            "  doc.txt: foo -> hello\\nworld",
        ],
    )


def test_fix_output_escapes_control_characters_in_paths(
    typos_plugin: TyposPlugin,
    tmp_path: Path,
) -> None:
    """A file name cannot forge listing entries or emit terminal escapes.

    Args:
        typos_plugin: Plugin fixture with version checking mocked out.
        tmp_path: Pytest temporary directory fixture.
    """
    evil = "evil\nFORGED:0: foo -> bar\x1b[31m.txt"
    detect = _found(_finding(1, 0, "teh", ["the"], path=evil))

    output = _fix_output(typos_plugin, tmp_path, detect=detect, recheck=_CLEAN)

    assert_that(output.splitlines()).is_equal_to(
        [
            "Fixed 1 typo(s).",
            "  evil\\nFORGED:0: foo -> bar\\x1b[31m.txt:1: teh -> the",
        ],
    )


def test_retry_keeps_corrections_from_passes_before_the_last(
    typos_plugin: TyposPlugin,
    tmp_path: Path,
) -> None:
    """An unfixable leftover triggers retries that write nothing; the list stays.

    Args:
        typos_plugin: Plugin fixture with version checking mocked out.
        tmp_path: Pytest temporary directory fixture.
    """
    leftover = _finding(2, 0, "CONNEC", ["CONNECT", "CONNED"])
    first = _found(_finding(1, 0, "teh", ["the"]), leftover)
    later = _found(leftover)
    passes = [first, _CLEAN, later, later, _CLEAN, later, later, _CLEAN, later]

    with patch.object(typos_plugin, "_run_subprocess_result", side_effect=passes):
        result = run_fix_with_retry(typos_plugin, [_target(tmp_path)], {}, 3)

    assert_that(result.fixed_issues_count).is_equal_to(1)
    assert_that(result.output).contains("  doc.txt:1: teh -> the")
    assert_that(result.metadata[APPLIED_CHANGES_KEY]).is_equal_to(
        ["doc.txt:1: teh -> the"],
    )


def test_write_pass_never_renames_files(
    typos_plugin: TyposPlugin,
    tmp_path: Path,
) -> None:
    """Only the write pass skips file names, so name typos are still found.

    Args:
        typos_plugin: Plugin fixture with version checking mocked out.
        tmp_path: Pytest temporary directory fixture.
    """
    commands: list[list[str]] = []

    def _record(cmd: list[str], **_kwargs: object) -> SubprocessResult:
        commands.append(cmd)
        return _CLEAN

    with patch.object(typos_plugin, "_run_subprocess_result", side_effect=_record):
        typos_plugin.fix([_target(tmp_path)], {})

    skips = ["--no-check-filenames" in cmd for cmd in commands]
    writes = ["--write-changes" in cmd for cmd in commands]
    assert_that(skips).is_equal_to(writes)
    assert_that(writes).is_equal_to([False, True, False])

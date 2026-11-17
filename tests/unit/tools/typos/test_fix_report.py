"""Tests that ``typos`` fix output lists every correction it wrote (#3509)."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from unittest.mock import patch

from assertpy import assert_that

from lintro.plugins.subprocess_executor import SubprocessResult
from lintro.tools.typos.definition import TyposPlugin
from lintro.utils.execution.fix_retry import run_fix_with_retry

_HEADER = (
    "Corrections written, from the scan taken just before the fix "
    "(line numbers refer to the file before the fix):"
)
_CLEAN = SubprocessResult(returncode=0, stdout="", stderr="", output="")


def _finding(
    line: int,
    typo: str,
    corrections: list[str],
    path: str = "doc.txt",
) -> str:
    """Build one JSON line of ``typos --format json`` output.

    Args:
        line: 1-based line number (0 for a file-name finding).
        typo: Misspelled word.
        corrections: Suggested replacements.
        path: Reported file path.

    Returns:
        A JSON-encoded typos finding.
    """
    record: dict[str, object] = {
        "type": "typo",
        "path": path,
        "byte_offset": 0,
        "typo": typo,
        "corrections": corrections,
    }
    if line:
        record["line_num"] = line
    return json.dumps(record)


def _found(*lines: str) -> SubprocessResult:
    """Build the result of a typos scan that reported findings.

    Args:
        *lines: JSON finding lines.

    Returns:
        A SubprocessResult with exit code 2 and the findings on stdout.
    """
    stdout = "\n".join(lines) + "\n"
    return SubprocessResult(returncode=2, stdout=stdout, stderr="", output=stdout)


def _typos(
    scans: list[SubprocessResult],
    targets: list[Path],
    writes: bool = True,
) -> Callable[..., SubprocessResult]:
    """Stand in for the typos binary over canned scans.

    Args:
        scans: Results of the detect and re-check scans, in call order.
        targets: Files a ``--write-changes`` call rewrites.
        writes: Whether a write call changes the targets' bytes.

    Returns:
        A ``_run_subprocess_result`` replacement.
    """
    queue: Iterator[SubprocessResult] = iter(scans)

    def _run(cmd: list[str], **_kwargs: object) -> SubprocessResult:
        if "--write-changes" not in cmd:
            return next(queue)
        for target in targets if writes else []:
            target.write_text(target.read_text() + "written\n")
        return _CLEAN

    return _run


def _target(tmp_path: Path, name: str = "doc.txt") -> Path:
    """Create a file the canned runs point at.

    Args:
        tmp_path: Directory to create it in.
        name: File name.

    Returns:
        The file's path.
    """
    target = tmp_path / name
    target.write_text("placeholder\n")
    return target


def _fix_lines(
    plugin: TyposPlugin,
    tmp_path: Path,
    scans: list[SubprocessResult],
    writes: bool = True,
) -> list[str]:
    """Run one ``fix`` over ``doc.txt`` and split its output into lines.

    Args:
        plugin: Plugin under test.
        tmp_path: Directory holding the target file.
        scans: Detect and re-check results.
        writes: Whether the write pass changes the file.

    Returns:
        The fix result's display output, one entry per line.
    """
    target = _target(tmp_path)
    with patch.object(
        plugin,
        "_run_subprocess_result",
        side_effect=_typos(scans, [target], writes=writes),
    ):
        result = plugin.fix([str(target)], {})
    return (result.output or "").splitlines()


def test_fix_output_lists_each_applied_correction(
    typos_plugin: TyposPlugin,
    tmp_path: Path,
) -> None:
    """Every rewrite appears as ``file:line: old -> new`` under the summary.

    Args:
        typos_plugin: Plugin fixture with version checking mocked out.
        tmp_path: Pytest temporary directory fixture.
    """
    detect = _found(_finding(1, "teh", ["the"]), _finding(3, "DELET", ["DELETE"]))

    lines = _fix_lines(typos_plugin, tmp_path, [detect, _CLEAN])

    assert_that(lines).is_equal_to(
        [
            "Fixed 2 typo(s).",
            _HEADER,
            "  doc.txt:1: teh -> the",
            "  doc.txt:3: DELET -> DELETE",
        ],
    )


def test_fix_output_skips_ambiguous_and_file_name_findings(
    typos_plugin: TyposPlugin,
    tmp_path: Path,
) -> None:
    """Only findings ``--write-changes`` applies are listed.

    A word with several candidates is left alone by typos, and file names are
    never rewritten because the write pass skips them.

    Args:
        typos_plugin: Plugin fixture with version checking mocked out.
        tmp_path: Pytest temporary directory fixture.
    """
    ambiguous = _finding(2, "CONNEC", ["CONNECT", "CONNED"])
    file_name = _finding(0, "recieve", ["receive"])
    detect = _found(_finding(1, "teh", ["the"]), ambiguous, file_name)

    lines = _fix_lines(
        typos_plugin,
        tmp_path,
        [detect, _found(ambiguous, file_name)],
    )

    assert_that(lines).is_equal_to(
        [
            "Fixed 1 typo(s); 2 could not be auto-corrected.",
            _HEADER,
            "  doc.txt:1: teh -> the",
        ],
    )


def test_fix_output_omits_files_whose_bytes_did_not_change(
    typos_plugin: TyposPlugin,
    tmp_path: Path,
) -> None:
    """A finding is not claimed as written when its file is byte-identical.

    Args:
        typos_plugin: Plugin fixture with version checking mocked out.
        tmp_path: Pytest temporary directory fixture.
    """
    detect = _found(_finding(1, "teh", ["the"]))

    lines = _fix_lines(typos_plugin, tmp_path, [detect, _CLEAN], writes=False)

    assert_that(lines).is_equal_to(["Fixed 1 typo(s)."])


def test_fix_output_keeps_pre_fix_lines_when_a_replacement_adds_lines(
    typos_plugin: TyposPlugin,
    tmp_path: Path,
) -> None:
    r"""A multi-line replacement is listed at its pre-fix line.

    With ``foo = "hello\nworld"`` configured, ``foo`` on line 1 becomes two
    lines and the ambiguous ``CONNEC`` moves from line 2 to line 3 untouched.
    It must not be listed as corrected (review of #3515).

    Args:
        typos_plugin: Plugin fixture with version checking mocked out.
        tmp_path: Pytest temporary directory fixture.
    """
    ambiguous = ["CONNECT", "CONNED"]
    detect = _found(
        _finding(1, "foo", ["hello\nworld"]),
        _finding(2, "CONNEC", ambiguous),
    )

    lines = _fix_lines(
        typos_plugin,
        tmp_path,
        [detect, _found(_finding(3, "CONNEC", ambiguous))],
    )

    assert_that(lines).is_equal_to(
        [
            "Fixed 1 typo(s); 1 could not be auto-corrected.",
            _HEADER,
            "  doc.txt:1: foo -> hello\\nworld",
        ],
    )


def test_fix_output_escapes_file_names(
    typos_plugin: TyposPlugin,
    tmp_path: Path,
) -> None:
    """File names cannot forge entries, emit escapes, or collide when shown.

    Args:
        typos_plugin: Plugin fixture with version checking mocked out.
        tmp_path: Pytest temporary directory fixture.
    """
    evil = "evil\nFORGED:0: foo -> bar\x1b[31m.txt"
    newline, backslash_n = "a\nb.txt", "a\\nb.txt"
    targets = [_target(tmp_path, name) for name in (evil, newline, backslash_n)]
    detect = _found(
        *(_finding(1, "teh", ["the"], path=t.name) for t in targets),
    )

    with patch.object(
        typos_plugin,
        "_run_subprocess_result",
        side_effect=_typos([detect, _CLEAN], targets),
    ):
        result = typos_plugin.fix([str(t) for t in targets], {})

    assert_that((result.output or "").splitlines()).is_equal_to(
        [
            "Fixed 3 typo(s).",
            _HEADER,
            "  evil\\nFORGED:0: foo -> bar\\x1b[31m.txt:1: teh -> the",
            "  a\\nb.txt:1: teh -> the",
            "  a\\\\nb.txt:1: teh -> the",
        ],
    )


def test_retry_lists_a_rewrite_that_introduced_the_same_word(
    typos_plugin: TyposPlugin,
    tmp_path: Path,
) -> None:
    r"""A rewrite never nets out against a typo another rewrite introduced.

    With ``foo = "teh"`` configured, ``foo\nteh\n`` becomes ``teh\nthe\n``
    in the first pass and ``the\nthe\n`` in the second (review of #3515).
    The earlier pass's rewrites lead, then the final pass's report.

    Args:
        typos_plugin: Plugin fixture with version checking mocked out.
        tmp_path: Pytest temporary directory fixture.
    """
    target = _target(tmp_path)
    first = _found(_finding(1, "foo", ["teh"]), _finding(2, "teh", ["the"]))
    introduced = _found(_finding(1, "teh", ["the"]))
    scans = [first, introduced, introduced, _CLEAN]

    with patch.object(
        typos_plugin,
        "_run_subprocess_result",
        side_effect=_typos(scans, [target]),
    ):
        result = run_fix_with_retry(typos_plugin, [str(target)], {}, 3)

    assert_that((result.output or "").splitlines()).is_equal_to(
        [
            "Applied by earlier fix passes:",
            "  doc.txt:1: foo -> teh",
            "  doc.txt:2: teh -> the",
            "Fixed 1 typo(s).",
            _HEADER,
            "  doc.txt:1: teh -> the",
        ],
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
        typos_plugin.fix([str(_target(tmp_path))], {})

    skips = ["--no-check-filenames" in cmd for cmd in commands]
    writes = ["--write-changes" in cmd for cmd in commands]
    assert_that(skips).is_equal_to(writes)
    assert_that(writes).is_equal_to([False, True, False])

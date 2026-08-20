"""Tests for typos plugin check and fix execution."""

from __future__ import annotations

import inspect
import json
import subprocess  # nosec B404 - subprocess is used to drive the tool/CLI under test; invocations use shell=False
from pathlib import Path
from unittest.mock import patch

from assertpy import assert_that

from lintro.plugins.subprocess_executor import SubprocessResult
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


def _proc(
    stdout: str = "",
    returncode: int = 0,
    stderr: str = "",
) -> SubprocessResult:
    """Build a SubprocessResult standing in for a real typos run.

    Args:
        stdout: Captured standard output (the JSON report).
        returncode: Exit code (typos uses 2 to signal findings).
        stderr: Captured standard error.

    Returns:
        SubprocessResult with the given streams.
    """
    return SubprocessResult(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        output=stdout + stderr,
    )


def _one_file_per_batch(paths: list[str], **_kwargs: object) -> list[list[str]]:
    """Chunk every path into its own batch.

    Args:
        paths: Paths to chunk.
        **_kwargs: Ignored budget arguments.

    Returns:
        One single-element batch per path.
    """
    return [[path] for path in paths]


def test_check_success_when_clean(
    typos_plugin: TyposPlugin,
    tmp_path: Path,
) -> None:
    """Check returns success and no issues when typos finds nothing.

    Args:
        typos_plugin: Plugin fixture with version checking mocked out.
        tmp_path: Pytest temporary directory fixture.
    """
    target = tmp_path / "clean.txt"
    target.write_text("all good words here\n")

    with patch.object(typos_plugin, "_run_subprocess_result", return_value=_proc()):
        result = typos_plugin.check([str(target)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_reports_issues(
    typos_plugin: TyposPlugin,
    tmp_path: Path,
) -> None:
    """Check surfaces parsed issues and fails when typos are present.

    Args:
        typos_plugin: Plugin fixture with version checking mocked out.
        tmp_path: Pytest temporary directory fixture.
    """
    target = tmp_path / "bad.txt"
    target.write_text("teh cat\n")
    output = _typo_line("bad.txt", "teh", "the")

    with patch.object(
        typos_plugin,
        "_run_subprocess_result",
        return_value=_proc(stdout=output, returncode=2),
    ):
        result = typos_plugin.check([str(target)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)
    assert_that(getattr((result.issues or [])[0], "typo", None)).is_equal_to("teh")


def test_check_ignores_stderr_noise(
    typos_plugin: TyposPlugin,
    tmp_path: Path,
) -> None:
    """Warnings on stderr do not corrupt the stdout JSON report.

    Args:
        typos_plugin: Plugin fixture with version checking mocked out.
        tmp_path: Pytest temporary directory fixture.
    """
    target = tmp_path / "bad.txt"
    target.write_text("teh cat\n")

    with patch.object(
        typos_plugin,
        "_run_subprocess_result",
        return_value=_proc(
            stdout=_typo_line("bad.txt", "teh", "the"),
            returncode=2,
            stderr="warning: ignoring unreadable file\n",
        ),
    ):
        result = typos_plugin.check([str(target)], {})

    assert_that(result.issues_count).is_equal_to(1)


def test_check_runtime_error_is_not_a_clean_pass(
    typos_plugin: TyposPlugin,
    tmp_path: Path,
) -> None:
    """A non-zero exit with no parseable report is reported as a failure.

    Args:
        typos_plugin: Plugin fixture with version checking mocked out.
        tmp_path: Pytest temporary directory fixture.
    """
    target = tmp_path / "bad.txt"
    target.write_text("content\n")

    with patch.object(
        typos_plugin,
        "_run_subprocess_result",
        return_value=_proc(returncode=1, stderr="error: invalid config\n"),
    ):
        result = typos_plugin.check([str(target)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.parse_failures_count).is_none()
    assert_that(result.output).contains("invalid config")


def test_check_timeout_returns_failure(
    typos_plugin: TyposPlugin,
    tmp_path: Path,
) -> None:
    """Check handles a subprocess timeout gracefully.

    Args:
        typos_plugin: Plugin fixture with version checking mocked out.
        tmp_path: Pytest temporary directory fixture.
    """
    target = tmp_path / "slow.txt"
    target.write_text("content\n")

    with patch.object(
        typos_plugin,
        "_run_subprocess_result",
        side_effect=subprocess.TimeoutExpired(cmd=["typos"], timeout=30),
    ):
        result = typos_plugin.check([str(target)], {})

    assert_that(result.success).is_false()
    assert_that(result.timed_out).is_true()
    assert_that(result.output).contains("timed out")


def test_fix_corrects_all_typos(
    typos_plugin: TyposPlugin,
    tmp_path: Path,
) -> None:
    """Fix reports every typo as fixed when the re-check is clean.

    Args:
        typos_plugin: Plugin fixture with version checking mocked out.
        tmp_path: Pytest temporary directory fixture.
    """
    target = tmp_path / "fixme.txt"
    target.write_text("teh cat\n")
    initial = _typo_line("fixme.txt", "teh", "the")

    # Sequence: initial check, write-changes, re-check (clean).
    with patch.object(
        typos_plugin,
        "_run_subprocess_result",
        side_effect=[
            _proc(stdout=initial, returncode=2),
            _proc(),
            _proc(),
        ],
    ):
        result = typos_plugin.fix([str(target)], {})

    assert_that(result.success).is_true()
    assert_that(result.initial_issues_count).is_equal_to(1)
    assert_that(result.fixed_issues_count).is_equal_to(1)
    assert_that(result.remaining_issues_count).is_equal_to(0)
    # Invariant: initial == fixed + remaining.
    assert_that(
        (result.fixed_issues_count or 0) + (result.remaining_issues_count or 0),
    ).is_equal_to(result.initial_issues_count)


def test_fix_partial_leaves_remaining(
    typos_plugin: TyposPlugin,
    tmp_path: Path,
) -> None:
    """Fix reports remaining typos when the re-check still finds one.

    Args:
        typos_plugin: Plugin fixture with version checking mocked out.
        tmp_path: Pytest temporary directory fixture.
    """
    target = tmp_path / "fixme.txt"
    target.write_text("teh seperate cat\n")
    initial = "\n".join(
        [_typo_line("fixme.txt", "teh", "the"), _typo_line("fixme.txt", "xyz", "abc")],
    )
    remaining = _typo_line("fixme.txt", "xyz", "abc")

    with patch.object(
        typos_plugin,
        "_run_subprocess_result",
        side_effect=[
            _proc(stdout=initial, returncode=2),
            _proc(),
            _proc(stdout=remaining, returncode=2),
        ],
    ):
        result = typos_plugin.fix([str(target)], {})

    assert_that(result.success).is_false()
    assert_that(result.initial_issues_count).is_equal_to(2)
    assert_that(result.fixed_issues_count).is_equal_to(1)
    assert_that(result.remaining_issues_count).is_equal_to(1)


def test_fix_write_failure_reports_error(
    typos_plugin: TyposPlugin,
    tmp_path: Path,
) -> None:
    """A failing ``--write-changes`` pass counts nothing as fixed.

    Args:
        typos_plugin: Plugin fixture with version checking mocked out.
        tmp_path: Pytest temporary directory fixture.
    """
    target = tmp_path / "fixme.txt"
    target.write_text("teh cat\n")
    initial = _typo_line("fixme.txt", "teh", "the")

    with patch.object(
        typos_plugin,
        "_run_subprocess_result",
        side_effect=[
            _proc(stdout=initial, returncode=2),
            _proc(returncode=1, stderr="error: read-only file system\n"),
        ],
    ):
        result = typos_plugin.fix([str(target)], {})

    assert_that(result.success).is_false()
    assert_that(result.fixed_issues_count).is_equal_to(0)
    assert_that(result.remaining_issues_count).is_equal_to(1)
    assert_that(result.output).contains("read-only file system")


def test_fix_timeout_returns_failure(
    typos_plugin: TyposPlugin,
    tmp_path: Path,
) -> None:
    """Fix handles a timeout during the initial detection pass.

    Args:
        typos_plugin: Plugin fixture with version checking mocked out.
        tmp_path: Pytest temporary directory fixture.
    """
    target = tmp_path / "slow.txt"
    target.write_text("teh\n")

    with patch.object(
        typos_plugin,
        "_run_subprocess_result",
        side_effect=subprocess.TimeoutExpired(cmd=["typos"], timeout=30),
    ):
        result = typos_plugin.fix([str(target)], {})

    assert_that(result.success).is_false()
    assert_that(result.timed_out).is_true()
    assert_that(result.output).contains("timed out")


def test_fix_does_not_write_when_initial_check_fails(
    typos_plugin: TyposPlugin,
    tmp_path: Path,
) -> None:
    """A failed detection pass must not be followed by ``--write-changes``.

    Args:
        typos_plugin: Plugin fixture with version checking mocked out.
        tmp_path: Pytest temporary directory fixture.
    """
    target = tmp_path / "fixme.txt"
    target.write_text("teh cat\n")
    commands: list[list[str]] = []

    def _record(cmd: list[str], **_kwargs: object) -> SubprocessResult:
        commands.append(cmd)
        return _proc(returncode=1, stderr="error: invalid config\n")

    with patch.object(typos_plugin, "_run_subprocess_result", side_effect=_record):
        result = typos_plugin.fix([str(target)], {})

    assert_that(commands).is_length(1)
    assert_that(commands[0]).does_not_contain("--write-changes")
    assert_that(result.success).is_false()
    assert_that(result.fixed_issues_count).is_equal_to(0)
    assert_that(result.output).contains("invalid config")


def test_binary_files_never_reach_the_command_line(
    typos_plugin: TyposPlugin,
    tmp_path: Path,
) -> None:
    """A NUL-containing file is dropped before ``--write-changes`` is built.

    Args:
        typos_plugin: Plugin fixture with version checking mocked out.
        tmp_path: Pytest temporary directory fixture.
    """
    text = tmp_path / "notes.txt"
    text.write_text("teh cat\n")
    binary = tmp_path / "logo.png"
    binary.write_bytes(b"\x89PNG\r\n\x1a\n\x00binary")
    commands: list[list[str]] = []

    def _record(cmd: list[str], **_kwargs: object) -> SubprocessResult:
        commands.append(cmd)
        return _proc()

    with patch.object(typos_plugin, "_run_subprocess_result", side_effect=_record):
        typos_plugin.fix([str(text), str(binary)], {})

    assert_that(commands).is_not_empty()
    for cmd in commands:
        assert_that(cmd).does_not_contain("logo.png")
    write_commands = [c for c in commands if "--write-changes" in c]
    assert_that(write_commands).is_not_empty()
    for cmd in write_commands:
        assert_that(cmd).contains("notes.txt")


def test_only_binary_inputs_skip_the_tool_entirely(
    typos_plugin: TyposPlugin,
    tmp_path: Path,
) -> None:
    """When every candidate is binary, typos is never invoked.

    Args:
        typos_plugin: Plugin fixture with version checking mocked out.
        tmp_path: Pytest temporary directory fixture.
    """
    binary = tmp_path / "logo.png"
    binary.write_bytes(b"\x89PNG\r\n\x1a\n\x00binary")

    with patch.object(typos_plugin, "_run_subprocess_result") as run:
        result = typos_plugin.fix([str(binary)], {})

    run.assert_not_called()
    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_large_file_lists_are_split_into_batches(
    typos_plugin: TyposPlugin,
    tmp_path: Path,
) -> None:
    """A file list wider than the argv budget is scanned in several batches.

    Args:
        typos_plugin: Plugin fixture with version checking mocked out.
        tmp_path: Pytest temporary directory fixture.
    """
    targets = []
    for index in range(5):
        target = tmp_path / f"file_{index}.txt"
        target.write_text("all good words here\n")
        targets.append(str(target))
    commands: list[list[str]] = []

    def _record(cmd: list[str], **_kwargs: object) -> SubprocessResult:
        commands.append(cmd)
        return _proc()

    # Force one path per batch so the batching loop is actually exercised.
    with (
        patch("lintro.tools.definitions.typos.chunk_paths", _one_file_per_batch),
        patch.object(typos_plugin, "_run_subprocess_result", side_effect=_record),
    ):
        result = typos_plugin.check(targets, {})

    assert_that(commands).is_length(5)
    assert_that(result.success).is_true()


def test_check_surfaces_a_failed_batch_alongside_findings(
    typos_plugin: TyposPlugin,
    tmp_path: Path,
) -> None:
    """One failing batch is reported even when another batch found typos.

    Args:
        typos_plugin: Plugin fixture with version checking mocked out.
        tmp_path: Pytest temporary directory fixture.
    """
    good = tmp_path / "aaa_good.txt"
    good.write_text("teh cat\n")
    bad = tmp_path / "zzz_bad.txt"
    bad.write_text("some words\n")

    def _respond(cmd: list[str], **_kwargs: object) -> SubprocessResult:
        if any(arg.endswith("zzz_bad.txt") for arg in cmd):
            return _proc(returncode=1, stderr="error: unreadable path\n")
        return _proc(stdout=_typo_line("aaa_good.txt", "teh", "the"), returncode=2)

    with (
        patch("lintro.tools.definitions.typos.chunk_paths", _one_file_per_batch),
        patch.object(typos_plugin, "_run_subprocess_result", side_effect=_respond),
    ):
        result = typos_plugin.check([str(good), str(bad)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)
    assert_that(result.parse_failures_count).is_none()
    assert_that(result.output).contains("unreadable path")


def test_fix_does_not_write_when_one_detect_batch_fails(
    typos_plugin: TyposPlugin,
    tmp_path: Path,
) -> None:
    """A single failed detection batch stops the whole write pass.

    Args:
        typos_plugin: Plugin fixture with version checking mocked out.
        tmp_path: Pytest temporary directory fixture.
    """
    good = tmp_path / "aaa_good.txt"
    good.write_text("teh cat\n")
    bad = tmp_path / "zzz_bad.txt"
    bad.write_text("some words\n")
    commands: list[list[str]] = []

    def _respond(cmd: list[str], **_kwargs: object) -> SubprocessResult:
        commands.append(cmd)
        if any(arg.endswith("zzz_bad.txt") for arg in cmd):
            return _proc(returncode=1, stderr="error: unreadable path\n")
        return _proc(stdout=_typo_line("aaa_good.txt", "teh", "the"), returncode=2)

    with (
        patch("lintro.tools.definitions.typos.chunk_paths", _one_file_per_batch),
        patch.object(typos_plugin, "_run_subprocess_result", side_effect=_respond),
    ):
        result = typos_plugin.fix([str(good), str(bad)], {})

    assert_that([c for c in commands if "--write-changes" in c]).is_empty()
    assert_that(result.success).is_false()
    assert_that(result.fixed_issues_count).is_equal_to(0)
    assert_that(result.output).contains("unreadable path")


def test_fix_reports_a_failed_write_batch(
    typos_plugin: TyposPlugin,
    tmp_path: Path,
) -> None:
    """A failed ``--write-changes`` batch is not reported as remaining typos.

    Args:
        typos_plugin: Plugin fixture with version checking mocked out.
        tmp_path: Pytest temporary directory fixture.
    """
    good = tmp_path / "aaa_good.txt"
    good.write_text("teh cat\n")
    bad = tmp_path / "zzz_bad.txt"
    bad.write_text("teh dog\n")

    def _respond(cmd: list[str], **_kwargs: object) -> SubprocessResult:
        writing = "--write-changes" in cmd
        target_is_bad = any(arg.endswith("zzz_bad.txt") for arg in cmd)
        if writing and target_is_bad:
            return _proc(returncode=1, stderr="error: read-only file system\n")
        name = "zzz_bad.txt" if target_is_bad else "aaa_good.txt"
        return _proc(stdout=_typo_line(name, "teh", "the"), returncode=2)

    with (
        patch("lintro.tools.definitions.typos.chunk_paths", _one_file_per_batch),
        patch.object(typos_plugin, "_run_subprocess_result", side_effect=_respond),
    ):
        result = typos_plugin.fix([str(good), str(bad)], {})

    assert_that(result.success).is_false()
    assert_that(result.fixed_issues_count).is_equal_to(0)
    assert_that(result.output).contains("read-only file system")


def test_check_reports_an_error_record_beside_findings_in_one_batch(
    typos_plugin: TyposPlugin,
    tmp_path: Path,
) -> None:
    """A per-file error is surfaced even when the same batch found typos.

    Args:
        typos_plugin: Plugin fixture with version checking mocked out.
        tmp_path: Pytest temporary directory fixture.
    """
    good = tmp_path / "good.txt"
    good.write_text("teh cat\n")
    bad = tmp_path / "bad.txt"
    bad.write_text("unreadable\n")
    stdout = "\n".join(
        [
            _typo_line("good.txt", "teh", "the"),
            '{"type":"error","path":"bad.txt","msg":"Permission denied"}',
        ],
    )

    with patch.object(
        typos_plugin,
        "_run_subprocess_result",
        return_value=_proc(stdout=stdout, returncode=1),
    ):
        result = typos_plugin.check([str(good), str(bad)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)
    assert_that(result.parse_failures_count).is_none()
    assert_that(result.output).contains("Permission denied")


def test_fix_does_not_write_when_a_batch_mixes_typos_and_an_error(
    typos_plugin: TyposPlugin,
    tmp_path: Path,
) -> None:
    """An error record in the detect pass blocks ``--write-changes``.

    Args:
        typos_plugin: Plugin fixture with version checking mocked out.
        tmp_path: Pytest temporary directory fixture.
    """
    good = tmp_path / "good.txt"
    good.write_text("teh cat\n")
    bad = tmp_path / "bad.txt"
    bad.write_text("unreadable\n")
    stdout = "\n".join(
        [
            _typo_line("good.txt", "teh", "the"),
            '{"type":"error","path":"bad.txt","msg":"Permission denied"}',
        ],
    )
    commands: list[list[str]] = []

    def _record(cmd: list[str], **_kwargs: object) -> SubprocessResult:
        commands.append(cmd)
        return _proc(stdout=stdout, returncode=1)

    with patch.object(typos_plugin, "_run_subprocess_result", side_effect=_record):
        result = typos_plugin.fix([str(good), str(bad)], {})

    assert_that([c for c in commands if "--write-changes" in c]).is_empty()
    assert_that(result.success).is_false()
    assert_that(result.output).contains("Permission denied")


def _three_files(tmp_path: Path) -> list[str]:
    """Create three checkable files with one typo each.

    Args:
        tmp_path: Pytest temporary directory fixture.

    Returns:
        Absolute paths of the created files, in a stable order.
    """
    targets = []
    for index in range(3):
        target = tmp_path / f"file_{index}.txt"
        target.write_text("teh cat\n")
        targets.append(str(target))
    return targets


def test_check_timeout_keeps_findings_from_earlier_batches(
    typos_plugin: TyposPlugin,
    tmp_path: Path,
) -> None:
    """A timed-out batch must not discard what earlier batches found.

    Args:
        typos_plugin: Plugin fixture with version checking mocked out.
        tmp_path: Pytest temporary directory fixture.
    """
    targets = _three_files(tmp_path)
    calls: list[list[str]] = []

    def _respond(cmd: list[str], **_kwargs: object) -> SubprocessResult:
        calls.append(cmd)
        if len(calls) == 1:
            return _proc(stdout=_typo_line("file_0.txt", "teh", "the"), returncode=2)
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=30)

    with (
        patch("lintro.tools.definitions.typos.chunk_paths", _one_file_per_batch),
        patch.object(typos_plugin, "_run_subprocess_result", side_effect=_respond),
    ):
        result = typos_plugin.check(targets, {})

    # The loop stops at the timeout rather than burning the budget three times.
    assert_that(calls).is_length(2)
    assert_that(result.success).is_false()
    assert_that(result.timed_out).is_true()
    assert_that(result.issues_count).is_equal_to(1)
    assert_that(result.output).contains("timed out")


def test_fix_write_pass_stops_after_a_failing_batch(
    typos_plugin: TyposPlugin,
    tmp_path: Path,
) -> None:
    """A fatal write batch must not keep rewriting the batches after it.

    Args:
        typos_plugin: Plugin fixture with version checking mocked out.
        tmp_path: Pytest temporary directory fixture.
    """
    targets = _three_files(tmp_path)
    write_calls: list[list[str]] = []

    def _respond(cmd: list[str], **_kwargs: object) -> SubprocessResult:
        if "--write-changes" not in cmd:
            name = next(a for a in cmd if a.endswith(".txt"))
            return _proc(stdout=_typo_line(name, "teh", "the"), returncode=2)
        write_calls.append(cmd)
        return _proc(returncode=1, stderr="error: read-only file system\n")

    with (
        patch("lintro.tools.definitions.typos.chunk_paths", _one_file_per_batch),
        patch.object(typos_plugin, "_run_subprocess_result", side_effect=_respond),
    ):
        result = typos_plugin.fix(targets, {})

    assert_that(write_calls).is_length(1)
    assert_that(result.success).is_false()
    assert_that(result.fixed_issues_count).is_equal_to(0)
    assert_that(result.output).contains("read-only file system")
    # --write-changes ran, so the caller must be told disk state is uncertain.
    assert_that(result.output).contains("may have been corrected on disk")


def test_fix_write_pass_timeout_flags_possible_disk_changes(
    typos_plugin: TyposPlugin,
    tmp_path: Path,
) -> None:
    """A timeout during the write pass reports the after-write caveat.

    Args:
        typos_plugin: Plugin fixture with version checking mocked out.
        tmp_path: Pytest temporary directory fixture.
    """
    targets = _three_files(tmp_path)

    def _respond(cmd: list[str], **_kwargs: object) -> SubprocessResult:
        if "--write-changes" in cmd:
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=30)
        name = next(a for a in cmd if a.endswith(".txt"))
        return _proc(stdout=_typo_line(name, "teh", "the"), returncode=2)

    with (
        patch("lintro.tools.definitions.typos.chunk_paths", _one_file_per_batch),
        patch.object(typos_plugin, "_run_subprocess_result", side_effect=_respond),
    ):
        result = typos_plugin.fix(targets, {})

    assert_that(result.success).is_false()
    assert_that(result.timed_out).is_true()
    assert_that(result.output).contains("timed out")
    assert_that(result.output).contains("may have been corrected on disk")
    assert_that(result.fixed_issues_count).is_equal_to(0)


def test_fix_failed_detect_keeps_sibling_findings(
    typos_plugin: TyposPlugin,
    tmp_path: Path,
) -> None:
    """Refusing to write must not also throw away what was detected.

    Args:
        typos_plugin: Plugin fixture with version checking mocked out.
        tmp_path: Pytest temporary directory fixture.
    """
    targets = _three_files(tmp_path)

    def _respond(cmd: list[str], **_kwargs: object) -> SubprocessResult:
        name = next(a for a in cmd if a.endswith(".txt"))
        if name.endswith("file_2.txt"):
            return _proc(returncode=1, stderr="error: unreadable path\n")
        return _proc(stdout=_typo_line(name, "teh", "the"), returncode=2)

    with (
        patch("lintro.tools.definitions.typos.chunk_paths", _one_file_per_batch),
        patch.object(typos_plugin, "_run_subprocess_result", side_effect=_respond),
    ):
        result = typos_plugin.fix(targets, {})

    assert_that(result.success).is_false()
    assert_that(result.initial_issues_count).is_equal_to(2)
    assert_that(result.issues_count).is_equal_to(2)
    assert_that(result.fixed_issues_count).is_equal_to(0)
    assert_that(result.output).contains("unreadable path")


def test_fix_recheck_failure_flags_possible_disk_changes(
    typos_plugin: TyposPlugin,
    tmp_path: Path,
) -> None:
    """A failing re-check reports the after-write caveat too.

    Args:
        typos_plugin: Plugin fixture with version checking mocked out.
        tmp_path: Pytest temporary directory fixture.
    """
    target = tmp_path / "fixme.txt"
    target.write_text("teh cat\n")
    calls: list[list[str]] = []

    def _respond(cmd: list[str], **_kwargs: object) -> SubprocessResult:
        calls.append(cmd)
        if len(calls) == 1:
            return _proc(stdout=_typo_line("fixme.txt", "teh", "the"), returncode=2)
        if "--write-changes" in cmd:
            return _proc()
        return _proc(returncode=1, stderr="error: config went missing\n")

    with patch.object(typos_plugin, "_run_subprocess_result", side_effect=_respond):
        result = typos_plugin.fix([str(target)], {})

    assert_that(result.success).is_false()
    assert_that(result.output).contains("config went missing")
    assert_that(result.output).contains("may have been corrected on disk")


def test_check_oserror_is_a_tool_failure(
    typos_plugin: TyposPlugin,
    tmp_path: Path,
) -> None:
    """E2BIG / vanished binary become a tool failure, not an executor crash.

    Args:
        typos_plugin: Plugin fixture with version checking mocked out.
        tmp_path: Pytest temporary directory fixture.
    """
    target = tmp_path / "notes.txt"
    target.write_text("plain text\n")

    with patch.object(
        typos_plugin,
        "_run_subprocess_result",
        side_effect=OSError(7, "Argument list too long"),
    ):
        result = typos_plugin.check([str(target)], {})

    assert_that(result.success).is_false()
    assert_that(result.output).contains("Argument list too long")
    assert_that(result.parse_failures_count).is_none()


def test_check_nonzero_non_findings_exit_is_fatal_even_with_issues(
    typos_plugin: TyposPlugin,
    tmp_path: Path,
) -> None:
    """Exit 1 with some JSON findings is a runtime failure, not a clean report.

    Args:
        typos_plugin: Plugin fixture with version checking mocked out.
        tmp_path: Pytest temporary directory fixture.
    """
    target = tmp_path / "bad.txt"
    target.write_text("teh cat\n")

    with patch.object(
        typos_plugin,
        "_run_subprocess_result",
        return_value=_proc(
            stdout=_typo_line("bad.txt", "teh", "the"),
            returncode=1,
            stderr="error: config problem\n",
        ),
    ):
        result = typos_plugin.check([str(target)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)
    assert_that(result.output).contains("config problem")


def test_fix_grows_initial_when_recheck_finds_more(
    typos_plugin: TyposPlugin,
    tmp_path: Path,
) -> None:
    """A re-check that grows must not raise ToolResult's count invariant.

    Args:
        typos_plugin: Plugin fixture with version checking mocked out.
        tmp_path: Pytest temporary directory fixture.
    """
    target = tmp_path / "fixme.txt"
    target.write_text("teh cat\n")
    initial = _typo_line("fixme.txt", "teh", "the")
    recheck = "\n".join(
        [
            _typo_line("fixme.txt", "teh", "the"),
            _typo_line("fixme.txt", "adn", "and"),
        ],
    )

    with patch.object(
        typos_plugin,
        "_run_subprocess_result",
        side_effect=[
            _proc(stdout=initial, returncode=2),
            _proc(),
            _proc(stdout=recheck, returncode=2),
        ],
    ):
        result = typos_plugin.fix([str(target)], {})

    assert_that(result.success).is_false()
    assert_that(result.remaining_issues_count).is_equal_to(2)
    assert_that(result.fixed_issues_count).is_equal_to(0)
    assert_that(result.initial_issues_count).is_equal_to(2)
    assert_that(
        (result.fixed_issues_count or 0) + (result.remaining_issues_count or 0),
    ).is_equal_to(result.initial_issues_count)


def test_plugin_uses_combined_typos_report_parser() -> None:
    """check, fix, write, and re-check all parse via ``parse_typos_report``.

    Fail-closed diagnostics live in the combined parser. A findings-only call
    would treat an error stream as a clean scan, so ``_run_batched`` must not
    invoke the two parsers independently.
    """
    source = inspect.getsource(TyposPlugin._run_batched)
    assert_that(source).contains("parse_typos_report")
    # Substring match also rejects the private split helpers
    # (``_parse_typos_output`` / ``_parse_typos_errors``).
    assert_that(source).does_not_contain("parse_typos_output")
    assert_that(source).does_not_contain("parse_typos_errors")


def test_fix_recheck_fails_on_error_record(
    typos_plugin: TyposPlugin,
    tmp_path: Path,
) -> None:
    """A diagnostic on the post-write re-check fails the ToolResult.

    Args:
        typos_plugin: Plugin fixture with version checking mocked out.
        tmp_path: Pytest temporary directory fixture.
    """
    target = tmp_path / "fixme.txt"
    target.write_text("teh cat\n")
    initial = _typo_line("fixme.txt", "teh", "the")
    recheck = '{"type":"error","path":"fixme.txt","msg":"Permission denied"}'

    with patch.object(
        typos_plugin,
        "_run_subprocess_result",
        side_effect=[
            _proc(stdout=initial, returncode=2),
            _proc(),
            _proc(stdout=recheck, returncode=1),
        ],
    ):
        result = typos_plugin.fix([str(target)], {})

    assert_that(result.success).is_false()
    assert_that(result.output).contains("Permission denied")

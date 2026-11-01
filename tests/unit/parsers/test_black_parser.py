"""Tests for Black parser utilities."""

from __future__ import annotations

from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.parsers.black.black_parser import (
    black_inspected_paths,
    black_report_parse_failures,
    parse_black_output,
)


@pytest.mark.parametrize("metadata_first", [True, False])
@pytest.mark.parametrize(
    ("metadata", "covered"),
    [
        ("line_length: 88", True),
        ("target_version: ['py311']", True),
        ("error: cannot format broken.py: Cannot parse: 1:0: invalid", False),
    ],
)
def test_black_coverage_distinguishes_configuration_metadata_from_errors(
    tmp_path: Path,
    metadata_first: bool,
    metadata: str,
    covered: bool,
) -> None:
    """Error lines invalidate coverage even beside a valid per-file confirmation."""
    file = tmp_path / "example.py"
    file.touch()
    confirmation = f"{file.name} already well formatted, good job."
    lines = [metadata, confirmation] if metadata_first else [confirmation, metadata]

    inspected = black_inspected_paths(
        "\n".join(lines),
        files=[file.name],
        cwd=str(tmp_path),
    )

    assert inspected == (frozenset({str(file)}) if covered else None)


def test_parse_black_output_would_reformat_single_file() -> None:
    """Parse a single-file 'would reformat' message into one issue."""
    output = (
        "would reformat src/app.py\nAll done! 💥 💔 💥\n1 file would be reformatted."
    )
    issues = parse_black_output(output)
    assert_that(issues).is_length(1)
    assert_that(issues[0].file).ends_with("src/app.py")
    assert_that(issues[0].message).contains("Would reformat")


def test_parse_black_output_reformatted_multiple_files() -> None:
    """Parse multi-file 'reformatted' output into per-file issues."""
    output = (
        "reformatted a.py\nreformatted b.py\nAll done! ✨ 🍰 ✨\n2 files reformatted"
    )
    issues = parse_black_output(output)
    files = {i.file for i in issues}
    assert_that(files).is_equal_to({"a.py", "b.py"})
    assert_that(all("Reformatted" in i.message for i in issues)).is_true()


def test_parse_black_output_no_issues() -> None:
    """Return empty list when Black reports no issues."""
    output = "All done! ✨ 🍰 ✨\n1 file left unchanged."
    issues = parse_black_output(output)
    assert_that(issues).is_equal_to([])


def test_parse_black_output_ansi_codes_stripped() -> None:
    """Strip ANSI escape codes from output for consistent CI/local parsing."""
    # Output with ANSI color codes (common in CI environments)
    output = "\x1b[1mwould reformat src/app.py\x1b[0m\n1 file would be reformatted."
    issues = parse_black_output(output)
    assert_that(issues).is_length(1)
    assert_that(issues[0].file).ends_with("src/app.py")


def test_parse_black_output_applied_uses_past_tense() -> None:
    """In applied (fix) context, 'would reformat' becomes 'Reformatted file'.

    Regression test for #1423: the pre-fix ``--check`` output used to build a
    fix result's detected issues must not read as preview-tense "Would
    reformat file" once the changes are applied.
    """
    output = "would reformat src/app.py\n1 file would be reformatted."
    issues = parse_black_output(output, applied=True)
    assert_that(issues).is_length(1)
    assert_that(issues[0].file).ends_with("src/app.py")
    assert_that(issues[0].message).is_equal_to("Reformatted file")


def test_parse_black_output_check_mode_keeps_preview_tense() -> None:
    """Default (check) context keeps the preview-tense 'Would reformat file'."""
    output = "would reformat src/app.py\n1 file would be reformatted."
    issues = parse_black_output(output)
    assert_that(issues[0].message).is_equal_to("Would reformat file")


@pytest.mark.parametrize("check", [True, False])
@pytest.mark.parametrize("unchanged,failed", [(0, 0), (1, 0), (0, 1), (3, 4)])
def test_native_summary_counts_include_trailing_clauses(
    check: bool,
    unchanged: int,
    failed: int,
) -> None:
    """Whole native summaries supply counts even with unchanged or failed files."""
    summary = "2 files " + ("would be reformatted" if check else "reformatted")
    if unchanged:
        tense = "would be left unchanged" if check else "left unchanged"
        summary += f", {unchanged} file{'s' if unchanged > 1 else ''} {tense}"
    if failed:
        tense = "would fail to reformat" if check else "failed to reformat"
        summary += f", {failed} file{'s' if failed > 1 else ''} {tense}"
    output = f"All done!\n\x1b[1m{summary}.\x1b[0m\n"

    issues = parse_black_output(output)

    assert_that(issues).is_length(2)
    assert_that([issue.file for issue in issues]).is_equal_to(["<unknown>"] * 2)
    assert_that(black_report_parse_failures(output, issues)).is_equal_to(2)
    per_file = ("would reformat" if check else "reformatted") + " a.py\n"
    partial_output = per_file + output
    assert_that(
        black_report_parse_failures(partial_output, parse_black_output(partial_output)),
    ).is_equal_to(1)


@pytest.mark.parametrize(
    "filename",
    [
        "2 files would be reformatted.py",
        "2 files reformatted.py",
        "src/2 files would be reformatted.py",
        "2 files would be reformatted, 1 file would be left unchanged.py",
        "2 files reformatted, 1 file failed to reformat.py",
    ],
)
@pytest.mark.parametrize("check", [True, False])
def test_summary_like_filenames_do_not_override_native_count(
    filename: str,
    check: bool,
) -> None:
    """A filename's apparent count cannot invalidate a fully parsed report."""
    diagnostic = "would reformat" if check else "reformatted"
    summary = "would be reformatted" if check else "reformatted"
    output = f"{diagnostic} {filename}\n1 file {summary}.\n"

    issues = parse_black_output(output)

    assert_that([issue.file for issue in issues]).is_equal_to([filename])
    assert_that(black_report_parse_failures(output, issues)).is_zero()
    assert_that(parse_black_output(filename)).is_empty()
    assert_that(
        parse_black_output(f"error: cannot format {filename}: invalid"),
    ).is_empty()

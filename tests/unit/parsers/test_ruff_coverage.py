"""Ruff coverage must authenticate identities before trusting line reports."""

from pathlib import Path

import pytest

from lintro.parsers.ruff.ruff_format_coverage import (
    format_inspected_paths,
    reported_inspected_paths,
)


def _coverage(
    paths: list[str],
    *,
    files: list[str],
    cwd: str | None,
) -> tuple[frozenset[str] | None, frozenset[str] | None]:
    """Read discovery and matching completed formatter evidence for the same paths."""
    trace = "".join(
        f"[ruff::commands::format][DEBUG] format_path; path={path}\n" for path in paths
    )
    trace += (
        f"[ruff::commands::format][DEBUG] Formatted {len(paths)} files in 1ms\n"
        f"{len(paths)} files already formatted\n"
    )
    return (
        reported_inspected_paths(paths, files=files, cwd=cwd),
        format_inspected_paths(trace, files=files, cwd=cwd),
    )


@pytest.mark.parametrize("working_directory", [None, "explicit"])
def test_coverage_accepts_only_the_reported_invocation_subset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    working_directory: str | None,
) -> None:
    """A native exclusion may omit an input while another earns exact coverage."""
    monkeypatch.chdir(tmp_path)
    file = tmp_path / "space name.py"
    file.touch()
    (tmp_path / "excluded.py").touch()
    cwd = str(tmp_path) if working_directory else None

    covered = _coverage([str(file)], files=[file.name, "excluded.py"], cwd=cwd)

    assert covered == (frozenset({str(file)}),) * 2
    assert _coverage([], files=[file.name], cwd=cwd) == (frozenset(),) * 2


@pytest.mark.parametrize(
    "report",
    ["foreign", "duplicate", "relative", "missing", "blank", "ansi", "alias"],
)
def test_existing_files_and_matching_counts_cannot_authenticate_foreign_reports(
    tmp_path: Path,
    report: str,
) -> None:
    """Even an existing alias or a duplicate with coherent counts is untrusted."""
    file = tmp_path / "actual.py"
    file.touch()
    foreign = tmp_path / "foreign.py"
    foreign.touch()
    alias = tmp_path / "alias.py"
    alias.symlink_to(file)
    paths = {
        "foreign": [str(foreign)],
        "duplicate": [str(file), str(file)],
        "relative": [file.name],
        "missing": [str(tmp_path / "missing.py")],
        "blank": [""],
        "ansi": [f"\x1b[31m{file}\x1b[0m"],
        "alias": [str(alias)],
    }[report]

    assert _coverage(paths, files=[file.name], cwd=str(tmp_path)) == (None, None)


@pytest.mark.parametrize(
    "separator",
    ["\n", "\r", "\r\n", "\v", "\f", "\x85", "\u2028", "\u2029", "\x1b[31m"],
)
@pytest.mark.parametrize("symlink", [False, True])
def test_ambiguous_invocation_or_resolved_name_invalidates_all_coverage(
    tmp_path: Path,
    separator: str,
    symlink: bool,
) -> None:
    """An ambiguous peer must invalidate even an otherwise valid victim report."""
    victim = tmp_path / "victim.py"
    victim.touch()
    ambiguous = tmp_path / f"prefix{separator}victim.py"
    ambiguous.touch()
    argument = ambiguous
    if symlink:
        argument = tmp_path / "safe_alias.py"
        argument.symlink_to(ambiguous)

    assert _coverage(
        [str(victim)],
        files=[str(argument), str(victim)],
        cwd=str(tmp_path),
    ) == (None, None)


def test_duplicate_resolved_identities_cannot_grant_formatter_coverage(
    tmp_path: Path,
) -> None:
    """Two invocation spellings of one file cannot satisfy a two-file summary."""
    file = tmp_path / "actual.py"
    file.touch()
    alias = tmp_path / "alias.py"
    alias.symlink_to(file)

    assert _coverage(
        [str(file), str(alias)],
        files=[file.name, alias.name],
        cwd=str(tmp_path),
    ) == (None, None)

"""Formatter debt tightens only after affirmative native file inspection."""

from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from lintro.cli import cli
from lintro.models.core.subprocess_result import SubprocessResult
from lintro.tools.black.definition import BlackPlugin
from lintro.tools.ruff.definition import RuffPlugin
from lintro.utils.fingerprint_baseline_file import read_baseline


@pytest.mark.parametrize("tool", ["ruff", "black"])
@pytest.mark.parametrize("mode", ["update", "prune"])
def test_fixed_formatter_file_loses_acknowledgement_before_regression(
    workspace: Path,
    tool: str,
    mode: str,
) -> None:
    """Real create/check/maintenance/check rejects previously fixed format debt."""
    file = workspace / "example.py"
    file.write_text("x=1\n", encoding="utf-8")
    runner = CliRunner()
    created = runner.invoke(cli, ["baseline", "create", file.name, "--tools", tool])
    assert created.exit_code == 0, created.output
    baseline = workspace / ".lintro-baseline.json"
    assert len(read_baseline(baseline).entries) == 1
    check_args = [
        "chk",
        file.name,
        "--tools",
        tool,
        "--baseline",
        str(baseline),
        "--yes",
    ]
    acknowledged = runner.invoke(cli, check_args)
    assert acknowledged.exit_code == 0, acknowledged.output
    assert "0 issues (1 baselined)" in acknowledged.output

    file.write_text("x = 1\n", encoding="utf-8")
    maintained = runner.invoke(cli, ["baseline", mode, file.name, "--tools", tool])
    assert maintained.exit_code == 0, maintained.output
    assert read_baseline(baseline).entries == []

    file.write_text("x=1\n", encoding="utf-8")
    regressed = runner.invoke(cli, check_args)
    assert regressed.exit_code == 1, regressed.output
    assert "1 issues (0 baselined)" in regressed.output


@pytest.mark.parametrize("tool", ["ruff", "black"])
@pytest.mark.parametrize("mode", ["update", "prune"])
def test_native_formatter_exclusion_retains_acknowledgement(
    workspace: Path,
    tool: str,
    mode: str,
) -> None:
    """Lint discovery and a zero formatter exit cannot prove an excluded file."""
    file = workspace / "example.py"
    file.write_text("x=1\n", encoding="utf-8")
    runner = CliRunner()
    created = runner.invoke(cli, ["baseline", "create", file.name, "--tools", tool])
    assert created.exit_code == 0, created.output
    baseline = workspace / ".lintro-baseline.json"
    before = baseline.read_bytes()
    native_config = (
        "[tool.ruff]\nforce-exclude = true\n"
        '[tool.ruff.format]\nexclude = ["example.py"]\n'
        if tool == "ruff"
        else '[tool.black]\nforce-exclude = "example.py"\n'
    )
    (workspace / "pyproject.toml").write_text(native_config, encoding="utf-8")
    file.write_text("x = 1\n", encoding="utf-8")

    maintained = runner.invoke(cli, ["baseline", mode, file.name, "--tools", tool])

    assert maintained.exit_code == 0, maintained.output
    assert baseline.read_bytes() == before


@pytest.mark.parametrize("mode", ["update", "prune"])
@pytest.mark.parametrize("separator", ["\n", "\r"])
def test_black_multiline_filename_cannot_prune_excluded_victim(
    workspace: Path,
    mode: str,
    separator: str,
) -> None:
    """A native confirmation's filename suffix cannot erase excluded format debt."""
    victim = workspace / "victim.py"
    victim.write_text("x=1\n", encoding="utf-8")
    runner = CliRunner()
    created = runner.invoke(
        cli,
        ["baseline", "create", victim.name, "--tools", "black"],
    )
    assert created.exit_code == 0, created.output
    baseline = workspace / ".lintro-baseline.json"
    assert len(read_baseline(baseline).entries) == 1
    before = baseline.read_bytes()
    (workspace / "pyproject.toml").write_text(
        '[tool.black]\nforce-exclude = "/victim[.]py$"\n',
        encoding="utf-8",
    )
    # Black excludes victim.py, but prints this clean file's name verbatim.
    (workspace / f"prefix{separator}victim.py").write_text("x = 1\n", encoding="utf-8")

    maintained = runner.invoke(cli, ["baseline", mode, ".", "--tools", "black"])

    assert maintained.exit_code == 0, maintained.output
    assert baseline.read_bytes() == before


@pytest.mark.parametrize(
    ("report", "returncode", "covered"),
    [
        ("victim.py already well formatted, good job.\n", 0, False),
        ("would reformat victim.py\n1 file would be reformatted.\n", 1, False),
        ("prefix\nexample.py already well formatted, good job.\n", 0, False),
        (
            "example.py already well formatted, good job.\n"
            "example.py wasn't modified on disk since last run.\n",
            0,
            False,
        ),
        (
            "Identified `.` as project root containing a pyproject.toml.\n"
            "Using configuration from project root.\nline_length: 88\n"
            'Found input source: "example.py"\n'
            "example.py already well formatted, good job.\n\n"
            "All done! ✨ 🍰 ✨\n1 file would be left unchanged.\n",
            0,
            True,
        ),
        ("would reformat ./example.py\n1 file would be reformatted.\n", 1, True),
    ],
    ids=[
        "foreign-clean",
        "foreign-changed",
        "fragment",
        "duplicate",
        "clean",
        "changed",
    ],
)
def test_black_coverage_validates_invocation_and_report(
    workspace: Path,
    report: str,
    returncode: int,
    covered: bool,
) -> None:
    """Existing files alone cannot authenticate native inspection confirmations."""
    file = workspace / "example.py"
    file.write_text("x = 1\n", encoding="utf-8")
    (workspace / "victim.py").write_text("x=1\n", encoding="utf-8")
    plugin = BlackPlugin()
    plugin.set_options(_baseline_coverage=True)
    with (
        patch.object(
            plugin,
            "_run_subprocess_result",
            return_value=SubprocessResult(returncode, "", report, report),
        ),
        patch.object(plugin, "_check_line_length_violations", return_value=[]),
    ):
        result = plugin.check([str(file)], {})

    assert result.findings_complete is True
    assert result.issues_count == returncode
    assert result.inspected_paths == (frozenset({str(file)}) if covered else None)


@pytest.mark.parametrize(
    ("report", "returncode", "covered"),
    [
        ("", 0, False),
        ("1 file would be left unchanged.\n", 0, False),
        ('Found input source: "example.py"\n', 0, False),
        ("example.py ignored: matches exclusion\n", 0, False),
        ("example.py already well formatted, good job.\n", 0, True),
        ("example.py wasn't modified on disk since last run.\n", 0, True),
        ("would reformat example.py\n1 file would be reformatted.\n", 1, True),
        ("would reformat example.py\n2 files would be reformatted.\n", 1, False),
        ("example.py already well formatted, good job.\n", 123, False),
    ],
)
def test_black_coverage_requires_completed_per_file_confirmation(
    workspace: Path,
    report: str,
    returncode: int,
    covered: bool,
) -> None:
    """Neither requested files, summary counts, discovery nor errors grant coverage."""
    file = workspace / "example.py"
    file.write_text("x = 1\n", encoding="utf-8")
    plugin = BlackPlugin()
    plugin.set_options(_baseline_coverage=True)
    with (
        patch.object(
            plugin,
            "_run_subprocess_result",
            return_value=SubprocessResult(returncode, "", report, report),
        ),
        patch.object(plugin, "_check_line_length_violations", return_value=[]),
    ):
        result = plugin.check([str(file)], {})

    assert bool(result.inspected_paths) is covered
    if covered:
        assert result.inspected_paths == frozenset({str(file)})
        # Black's confirmations do not prove its separate Ruff E501 inspection.
        assert "E501" in result.uninspected_codes


@pytest.mark.parametrize(
    ("format_check", "report", "returncode", "covered"),
    [
        (False, "", 0, False),
        (True, "", 0, False),
        (True, "1 file already formatted\n", 0, True),
        (True, "2 files already formatted\n", 0, False),
        (True, "Would reformat: example.py\n1 file would be reformatted\n", 1, True),
        (True, "Would reformat: example.py\n2 files would be reformatted\n", 1, False),
        (True, "1 file already formatted\n", 2, False),
    ],
)
@pytest.mark.parametrize("trace_complete", [False, True])
def test_ruff_format_coverage_requires_a_complete_format_check(
    workspace: Path,
    format_check: bool,
    report: str,
    returncode: int,
    covered: bool,
    trace_complete: bool,
) -> None:
    """Lint coverage cannot stand in for a disabled, partial or failed formatter."""
    file = workspace / "example.py"
    file.write_text("x = 1\n", encoding="utf-8")
    plugin = RuffPlugin()
    plugin.set_options(format_check=format_check, _baseline_coverage=True)
    trace = (
        (
            f"[ruff::commands::format][DEBUG] format_path; path={file}\n"
            "[ruff::commands::format][DEBUG] Formatted 1 files in 1ms\n"
        )
        if trace_complete
        else ""
    )

    def execute(cmd: list[str], **_kwargs: object) -> SubprocessResult:
        if "--show-files" in cmd:
            return SubprocessResult(0, str(file), "", str(file))
        if "format" in cmd:
            return SubprocessResult(returncode, report, trace, report + trace)
        return SubprocessResult(0, "[]", "", "[]")

    with patch.object(plugin, "_run_subprocess_result", side_effect=execute):
        result = plugin.check([str(file)], {})

    format_covered = (
        bool(result.inspected_paths) and "FORMAT" not in result.uninspected_codes
    )
    assert format_covered is (covered and trace_complete)
    if format_covered:
        assert result.inspected_paths == frozenset({str(file)})

"""Tests for osv_scanner exclusion handling (.lintro-ignore and --exclude).

osv-scanner performs its own recursive lockfile discovery, so lintro applies
its resolved exclusion set to the reported source paths. These tests cover
both directory-level and lockfile-level patterns, and assert that the two
entry points (``.lintro-ignore`` and ``--exclude``) behave identically
(lgtm-hq/py-lintro#1725).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.models.core.tool_result import ToolResult
from lintro.plugins.subprocess_executor import SubprocessResult
from lintro.tools.definitions.osv_scanner import OsvScannerPlugin

# Lockfiles laid out by the fixture repo, relative to the project root.
_MAIN_LOCKFILE: str = "apps/web/bun.lock"
_NESTED_LOCKFILES: tuple[str, ...] = (
    ".claude/worktrees/agent-1/apps/web/bun.lock",
    ".claude/worktrees/agent-2/apps/web/bun.lock",
)


def _result_for(source_path: str, vuln_id: str) -> dict[str, object]:
    """Build a single osv-scanner JSON result block.

    Args:
        source_path: Absolute path of the lockfile the finding came from.
        vuln_id: Vulnerability identifier to report.

    Returns:
        One entry of the osv-scanner ``results`` list.
    """
    return {
        "source": {"path": source_path, "type": "lockfile"},
        "packages": [
            {
                "package": {
                    "name": "vite",
                    "version": "5.0.0",
                    "ecosystem": "npm",
                },
                "groups": [{"ids": [vuln_id], "max_severity": "HIGH"}],
                "vulnerabilities": [{"id": vuln_id}],
            },
        ],
    }


def _payload(lockfiles: list[str]) -> str:
    """Build an osv-scanner JSON report reporting one finding per lockfile.

    Args:
        lockfiles: Absolute lockfile paths to report findings for.

    Returns:
        Serialized osv-scanner JSON report.
    """
    return json.dumps(
        {
            "results": [
                _result_for(path, f"GHSA-test-{index}")
                for index, path in enumerate(lockfiles)
            ],
        },
    )


@pytest.fixture
def fixture_repo(tmp_path: Path) -> Path:
    """Create a project containing a nested checkout with its own lockfile.

    Reproduces the finding-multiplication case: the same lockfile content is
    present in the project and again inside each nested worktree.

    Args:
        tmp_path: Pytest-provided temporary directory.

    Returns:
        Path to the project root.
    """
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\n")
    for relative in (_MAIN_LOCKFILE, *_NESTED_LOCKFILES):
        lockfile = tmp_path / relative
        lockfile.parent.mkdir(parents=True, exist_ok=True)
        lockfile.write_text('{"lockfileVersion": 1}\n')
    return tmp_path


@pytest.fixture
def in_fixture_repo(
    fixture_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Path]:
    """Change into the fixture repo so ignore-file discovery anchors there.

    Args:
        fixture_repo: The generated project root.
        monkeypatch: Pytest monkeypatch fixture.

    Yields:
        Path: The project root, with it set as the working directory.
    """
    monkeypatch.chdir(fixture_repo)
    yield fixture_repo


def _plugin() -> OsvScannerPlugin:
    """Build a plugin instance with its version check bypassed.

    Returns:
        A freshly constructed plugin, which resolves ``.lintro-ignore`` from
        the current working directory at construction time.
    """
    return OsvScannerPlugin()


def _run_check(
    plugin: OsvScannerPlugin,
    root: Path,
    lockfiles: list[str],
) -> ToolResult:
    """Run ``check`` against a mocked osv-scanner report.

    Args:
        plugin: Plugin under test.
        root: Project root passed as the scan path.
        lockfiles: Project-relative lockfile paths to report findings for.

    Returns:
        The ToolResult produced by the plugin.
    """
    stdout = _payload([str(root / relative) for relative in lockfiles])
    proc = SubprocessResult(
        returncode=1,
        stdout=stdout,
        stderr="",
        output=stdout,
    )
    with (
        patch(
            "lintro.tools.definitions.osv_scanner.verify_tool_version",
            return_value=None,
        ),
        patch.object(
            OsvScannerPlugin,
            "_run_subprocess_result",
            return_value=proc,
        ),
    ):
        return plugin.check(
            paths=[str(root)],
            options={"check_suppressions": False},
        )


def _reported_files(result: ToolResult) -> list[str]:
    """Extract the lockfile paths of the reported issues.

    Args:
        result: ToolResult from a check run.

    Returns:
        Sorted source paths of the surviving issues.
    """
    return sorted(issue.file for issue in (result.issues or []))


def test_baseline_reports_every_lockfile(in_fixture_repo: Path) -> None:
    """Without exclusions every discovered lockfile finding is reported."""
    result = _run_check(
        plugin=_plugin(),
        root=in_fixture_repo,
        lockfiles=[_MAIN_LOCKFILE, *_NESTED_LOCKFILES],
    )

    assert_that(result.issues_count).is_equal_to(3)
    assert_that(result.success).is_false()


def test_lintro_ignore_directory_excludes_nested_lockfiles(
    in_fixture_repo: Path,
) -> None:
    """A directory pattern in .lintro-ignore drops that tree's findings."""
    (in_fixture_repo / ".lintro-ignore").write_text(".claude\n")

    result = _run_check(
        plugin=_plugin(),
        root=in_fixture_repo,
        lockfiles=[_MAIN_LOCKFILE, *_NESTED_LOCKFILES],
    )

    assert_that(result.issues_count).is_equal_to(1)
    assert_that(_reported_files(result)).is_equal_to(
        [str(in_fixture_repo / _MAIN_LOCKFILE)],
    )


def test_lintro_ignore_comments_and_blanks_are_skipped(
    in_fixture_repo: Path,
) -> None:
    """Comment and blank lines in .lintro-ignore do not affect filtering."""
    (in_fixture_repo / ".lintro-ignore").write_text(
        "# nested agent checkouts\n\n.claude\n",
    )

    result = _run_check(
        plugin=_plugin(),
        root=in_fixture_repo,
        lockfiles=[_MAIN_LOCKFILE, *_NESTED_LOCKFILES],
    )

    assert_that(result.issues_count).is_equal_to(1)


@pytest.mark.parametrize("pattern", ["bun.lock", "*.lock"])
def test_lintro_ignore_lockfile_pattern_excludes_all_matches(
    in_fixture_repo: Path,
    pattern: str,
) -> None:
    """A file-level pattern in .lintro-ignore drops matching lockfiles.

    Args:
        in_fixture_repo: The generated project root, as the working directory.
        pattern: File-level ignore pattern under test.
    """
    (in_fixture_repo / ".lintro-ignore").write_text(f"{pattern}\n")

    result = _run_check(
        plugin=_plugin(),
        root=in_fixture_repo,
        lockfiles=[_MAIN_LOCKFILE, *_NESTED_LOCKFILES],
    )

    assert_that(result.issues_count).is_equal_to(0)
    # Nothing left to report, so the non-zero exit must not surface as a
    # failed scan.
    assert_that(result.success).is_true()


def test_exclude_flag_matches_lintro_ignore(in_fixture_repo: Path) -> None:
    """--exclude and .lintro-ignore produce an identical surviving set."""
    flag_plugin = _plugin()
    flag_plugin.set_options(exclude_patterns=[".claude"])
    flag_result = _run_check(
        plugin=flag_plugin,
        root=in_fixture_repo,
        lockfiles=[_MAIN_LOCKFILE, *_NESTED_LOCKFILES],
    )

    (in_fixture_repo / ".lintro-ignore").write_text(".claude\n")
    ignore_result = _run_check(
        plugin=_plugin(),
        root=in_fixture_repo,
        lockfiles=[_MAIN_LOCKFILE, *_NESTED_LOCKFILES],
    )

    assert_that(_reported_files(flag_result)).is_equal_to(
        _reported_files(ignore_result),
    )
    assert_that(flag_result.issues_count).is_equal_to(ignore_result.issues_count)


def test_exclude_flag_supports_lockfile_patterns(in_fixture_repo: Path) -> None:
    """--exclude accepts file-level lockfile patterns too."""
    plugin = _plugin()
    plugin.set_options(exclude_patterns=["*.lock"])

    result = _run_check(
        plugin=plugin,
        root=in_fixture_repo,
        lockfiles=[_MAIN_LOCKFILE, *_NESTED_LOCKFILES],
    )

    assert_that(result.issues_count).is_equal_to(0)


def test_unrelated_pattern_keeps_all_findings(in_fixture_repo: Path) -> None:
    """An exclusion that matches nothing leaves every finding intact."""
    (in_fixture_repo / ".lintro-ignore").write_text("vendor/\n")

    result = _run_check(
        plugin=_plugin(),
        root=in_fixture_repo,
        lockfiles=[_MAIN_LOCKFILE, *_NESTED_LOCKFILES],
    )

    assert_that(result.issues_count).is_equal_to(3)


def test_filter_keeps_placeholder_source(in_fixture_repo: Path) -> None:
    """Findings without a real source path are never filtered out."""
    from lintro.parsers.osv_scanner import OsvScannerIssue

    plugin = _plugin()
    plugin.set_options(exclude_patterns=["*.lock"])
    issue = OsvScannerIssue(
        file="",
        line=0,
        column=0,
        message="",
        vuln_id="GHSA-placeholder",
    )

    kept = plugin.filter_excluded_issues(
        issues=[issue],
        paths=[str(in_fixture_repo)],
    )

    assert_that(kept).is_length(1)


def test_filter_without_patterns_is_identity(in_fixture_repo: Path) -> None:
    """With no exclusion patterns the issue list is returned unchanged."""
    from lintro.parsers.osv_scanner import OsvScannerIssue

    plugin = _plugin()
    plugin.exclude_patterns = []
    issue = OsvScannerIssue(
        file=str(in_fixture_repo / _MAIN_LOCKFILE),
        line=0,
        column=0,
        message="",
        vuln_id="GHSA-none",
    )

    kept = plugin.filter_excluded_issues(
        issues=[issue],
        paths=[str(in_fixture_repo)],
    )

    assert_that(kept).is_length(1)


def test_execution_failure_still_fails_when_all_findings_excluded(
    in_fixture_repo: Path,
) -> None:
    """Unparseable output stays a failure even with exclusions configured."""
    (in_fixture_repo / ".lintro-ignore").write_text("*.lock\n")
    plugin = _plugin()
    proc = SubprocessResult(
        returncode=1,
        stdout="connection refused",
        stderr="",
        output="connection refused",
    )

    with (
        patch(
            "lintro.tools.definitions.osv_scanner.verify_tool_version",
            return_value=None,
        ),
        patch.object(
            OsvScannerPlugin,
            "_run_subprocess_result",
            return_value=proc,
        ),
    ):
        result = plugin.check(
            paths=[str(in_fixture_repo)],
            options={"check_suppressions": False},
        )

    assert_that(result.success).is_false()
    assert_that(result.parse_failures_count).is_equal_to(1)

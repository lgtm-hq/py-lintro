"""Tests for ShellcheckPlugin check command.

These tests verify the check command works correctly on various inputs.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from assertpy import assert_that

if TYPE_CHECKING:
    from lintro.parsers.base_issue import BaseIssue
    from lintro.plugins.base import BaseToolPlugin

pytestmark = pytest.mark.skipif(
    shutil.which("shellcheck") is None,
    reason="shellcheck not installed",
)


def test_check_file_with_issues(
    get_plugin: Callable[[str], BaseToolPlugin],
    shellcheck_violation_file: str,
) -> None:
    """Verify ShellCheck check detects lint issues in problematic files.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        shellcheck_violation_file: Path to file with lint issues.
    """
    shellcheck_plugin = get_plugin("shellcheck")
    result = shellcheck_plugin.check([shellcheck_violation_file], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("shellcheck")
    assert_that(result.issues_count).is_greater_than(0)


def test_check_clean_file(
    get_plugin: Callable[[str], BaseToolPlugin],
    shellcheck_clean_file: str,
) -> None:
    """Verify ShellCheck check passes on clean files.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        shellcheck_clean_file: Path to clean file.
    """
    shellcheck_plugin = get_plugin("shellcheck")
    result = shellcheck_plugin.check([shellcheck_clean_file], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("shellcheck")
    assert_that(result.success).is_true()


def test_check_empty_directory(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """Verify ShellCheck check handles empty directories gracefully.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    shellcheck_plugin = get_plugin("shellcheck")
    result = shellcheck_plugin.check([str(tmp_path)], {})

    assert_that(result).is_not_none()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_severity_filters_issues(
    get_plugin: Callable[[str], BaseToolPlugin],
    shellcheck_style_issues_file: str,
) -> None:
    """Verify ShellCheck severity option filters issues appropriately.

    Runs ShellCheck with error severity (strictest) and verifies fewer
    issues are found than with style severity (least strict).

    Args:
        get_plugin: Fixture factory to get plugin instances.
        shellcheck_style_issues_file: Path to file with style-level issues.
    """
    shellcheck_plugin = get_plugin("shellcheck")

    # Check with style severity (default, reports all issues)
    shellcheck_plugin.set_options(severity="style")
    style_result = shellcheck_plugin.check([shellcheck_style_issues_file], {})

    # Precondition: ensure we have issues to filter
    assert_that(style_result.issues_count).is_greater_than(0)

    # Check with error severity (strictest, reports only errors)
    shellcheck_plugin.set_options(severity="error")
    error_result = shellcheck_plugin.check([shellcheck_style_issues_file], {})

    # Error severity should report fewer or equal issues than style
    assert_that(error_result.issues_count).is_less_than_or_equal_to(
        style_result.issues_count,
    )


def test_check_exclude_filters_issues(
    get_plugin: Callable[[str], BaseToolPlugin],
    shellcheck_violation_file: str,
) -> None:
    """Verify ShellCheck exclude option filters out specific codes.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        shellcheck_violation_file: Path to file with lint issues.
    """
    shellcheck_plugin = get_plugin("shellcheck")

    # Check without exclusions
    result_without_exclude = shellcheck_plugin.check([shellcheck_violation_file], {})

    # Precondition: ensure we have issues to exclude
    assert_that(result_without_exclude.issues_count).is_greater_than(0)

    # Check with common codes excluded
    shellcheck_plugin.set_options(exclude=["SC2086", "SC2002", "SC2206"])
    result_with_exclude = shellcheck_plugin.check([shellcheck_violation_file], {})

    # With exclusions should report fewer or equal issues
    assert_that(result_with_exclude.issues_count).is_less_than_or_equal_to(
        result_without_exclude.issues_count,
    )


def _write_script_dir_sourcing_sample(tmp_path: Path) -> str:
    """Create a script that sources a repo-local helper via SCRIPT_DIR.

    Mirrors the runtime-safe sourcing pattern from issue #928, where a script
    computes its own directory and sources a sibling helper by relative path.

    Args:
        tmp_path: Pytest-provided temporary directory root.

    Returns:
        str: Path to the entrypoint script that performs the sourcing.
    """
    lib_dir = tmp_path / "scripts" / "lib"
    ci_dir = tmp_path / "scripts" / "ci"
    lib_dir.mkdir(parents=True)
    ci_dir.mkdir(parents=True)

    (lib_dir / "common.sh").write_text(
        "#!/usr/bin/env bash\ncommon_helper() {\n  echo 'hello'\n}\n",
    )
    entry = ci_dir / "run.sh"
    entry.write_text(
        "#!/usr/bin/env bash\n"
        'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
        'source "$SCRIPT_DIR/../lib/common.sh"\n'
        "common_helper\n",
    )
    return str(entry)


def _sc1091_count(issues: Sequence[BaseIssue] | None) -> int:
    """Count SC1091 ('not following source') issues in a result set.

    Args:
        issues: Parsed issue objects from a ShellCheck run.

    Returns:
        int: Number of issues whose code is SC1091.
    """
    return sum(
        1 for issue in (issues or []) if str(getattr(issue, "code", "")) == "SC1091"
    )


def test_check_reports_sc1091_without_source_following(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """Baseline: sourcing a repo-local helper emits SC1091 when not opted in.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    entry = _write_script_dir_sourcing_sample(tmp_path)
    shellcheck_plugin = get_plugin("shellcheck")
    result = shellcheck_plugin.check([entry], {})

    assert_that(_sc1091_count(result.issues)).is_greater_than(0)


def test_check_source_following_resolves_sc1091(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """external_sources + SCRIPTDIR source-path clears SC1091 for the pattern.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    entry = _write_script_dir_sourcing_sample(tmp_path)
    shellcheck_plugin = get_plugin("shellcheck")
    shellcheck_plugin.set_options(
        external_sources=True,
        source_paths=["SCRIPTDIR"],
    )
    result = shellcheck_plugin.check([entry], {})

    assert_that(_sc1091_count(result.issues)).is_equal_to(0)


def test_check_source_paths_alone_resolves_sc1091(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """source_paths alone clears SC1091: it auto-enables -x for ShellCheck.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    entry = _write_script_dir_sourcing_sample(tmp_path)
    shellcheck_plugin = get_plugin("shellcheck")
    # Deliberately omit external_sources; setting source_paths must imply it.
    shellcheck_plugin.set_options(source_paths=["SCRIPTDIR"])
    result = shellcheck_plugin.check([entry], {})

    assert_that(_sc1091_count(result.issues)).is_equal_to(0)

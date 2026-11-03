"""Real Ruff warnings must preserve statistics and fix measurements."""

from __future__ import annotations

from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.parsers.ruff.ruff_parser import parse_ruff_output_with_stats
from lintro.tools.ruff.commands import build_ruff_check_command
from lintro.tools.ruff.definition import RuffPlugin
from tests.integration._tools import require_tool

pytestmark = require_tool("ruff")


@pytest.fixture
def native_warning_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create native deprecated settings in a bracketed project path.

    Args:
        tmp_path: Temporary test directory.
        monkeypatch: Environment and working-directory isolation.

    Returns:
        Source path in the native Ruff project.
    """
    project = tmp_path / "project[cache]"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[tool.ruff]\nselect = ["F401", "F821"]\nignore = []\n',
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("LINTRO_TEST_MODE", raising=False)
    monkeypatch.setenv("LINTRO_SKIP_CONFIG_INJECTION", "1")
    return project / "a.py"


@pytest.mark.parametrize(
    ("source", "codes"),
    [("x = 1\n", []), ("import os\n", ["F401"])],
)
def test_native_multiline_warning_statistics(
    native_warning_project: Path,
    source: str,
    codes: list[str],
) -> None:
    """Combined real output has the same findings as native JSON stdout."""
    native_warning_project.write_text(source)
    tool = RuffPlugin()
    process = tool._run_subprocess_result(
        build_ruff_check_command(tool, [str(native_warning_project)]),
    )

    assert_that(process.returncode).is_equal_to(int(bool(codes)))
    assert_that(process.stderr).contains(
        "warning: The top-level linter settings are deprecated",
        "  - 'ignore' -> 'lint.ignore'",
        "  - 'select' -> 'lint.select'",
        "project[cache]",
    )
    issues, failures = parse_ruff_output_with_stats(process.output)
    assert_that(failures).is_zero()
    assert_that(issues).extracting("code").is_equal_to(codes)
    assert_that(parse_ruff_output_with_stats(process.stdout)).is_equal_to(
        (issues, failures),
    )


@pytest.mark.parametrize("unsafe_fixes", [False, True])
@pytest.mark.parametrize(
    ("source", "expected_source", "initial_codes", "remaining_codes"),
    [
        ("x = 1\n", "x = 1\n", [], []),
        ("import os\n", "", ["F401"], []),
        (
            "import os\nprint(undefined_name)\n",
            "print(undefined_name)\n",
            ["F401", "F821"],
            ["F821"],
        ),
    ],
)
def test_fix_with_native_multiline_warning(
    native_warning_project: Path,
    unsafe_fixes: bool,
    source: str,
    expected_source: str,
    initial_codes: list[str],
    remaining_codes: list[str],
) -> None:
    """Actual fix keeps the F821 residual after removing F401, including its probe."""
    native_warning_project.write_text(source)
    tool = RuffPlugin()
    checked = tool.check([str(native_warning_project)], {"format_check": False})
    assert_that(checked.parse_failures_count).is_zero()
    assert_that(checked.findings_complete).is_true()
    assert_that(checked.issues).extracting("code").is_equal_to(initial_codes)

    result = tool.fix(
        [str(native_warning_project)],
        {"format": False, "unsafe_fixes": unsafe_fixes},
    )

    assert_that(native_warning_project.read_text()).is_equal_to(expected_source)
    assert_that(result.initial_issues).extracting("code").is_equal_to(initial_codes)
    assert_that(result.issues).extracting("code").is_equal_to(remaining_codes)
    assert_that(result.parse_failures_count).is_zero()
    assert_that(result.initial_issues_count).is_equal_to(len(initial_codes))
    assert_that(result.fixed_issues_count).is_equal_to(
        len(initial_codes) - len(remaining_codes),
    )
    assert_that(result.remaining_issues_count).is_equal_to(len(remaining_codes))
    assert_that(result.issues_count).is_equal_to(len(remaining_codes))
    assert_that(result.success).is_equal_to(not remaining_codes)

"""Real Ruff warnings must preserve statistics and fix measurements."""

from __future__ import annotations

from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.parsers.base_parser import strip_ansi_codes
from lintro.parsers.ruff.ruff_parser import parse_ruff_output_with_stats
from lintro.tools.ruff.commands import build_ruff_check_command
from lintro.tools.ruff.definition import RuffPlugin
from tests.integration._tools import require_tool

pytestmark = require_tool("ruff")


@pytest.fixture(params=[None, "FORCE_COLOR", "CLICOLOR_FORCE"])
def native_warning_color(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> str | None:
    """Control native color with both forcing variables absent by default.

    Args:
        monkeypatch: Environment isolation.
        request: Color variable to enable, or None for unforced output.

    Returns:
        Enabled color variable, or None when both variables are unset.
    """
    color_variable: str | None = request.param
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.delenv("CLICOLOR_FORCE", raising=False)
    if color_variable is not None:
        monkeypatch.setenv(color_variable, "1")
    return color_variable


@pytest.fixture(params=["deprecated-settings", "ANN101", "UP027", "incompatible"])
def native_warning_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
    native_warning_color: str | None,
) -> tuple[Path, list[str]]:
    """Create native warning settings in a bracketed project path.

    Args:
        tmp_path: Temporary test directory.
        monkeypatch: Environment and working-directory isolation.
        request: Native warning configuration to exercise.
        native_warning_color: Isolated color forcing inherited by native Ruff.

    Returns:
        Source path and expected stderr fragments in the native Ruff project.
    """
    project = tmp_path / "project[cache]"
    project.mkdir()
    if request.param == "deprecated-settings":
        config = '[tool.ruff]\nselect = ["F401", "F821"]\nignore = []\n'
        warning_fragments = [
            "warning: The top-level linter settings are deprecated",
            "  - 'ignore' -> 'lint.ignore'",
            "  - 'select' -> 'lint.select'",
            "project[cache]",
        ]
    elif request.param == "incompatible":
        config = (
            "[tool.ruff.lint]\n"
            'select = ["F401", "F821", "D203", "D211", "D212", "D213"]\n'
        )
        warning_fragments = [
            "warning: `incorrect-blank-line-before-class` (D203) and "
            "`blank-line-before-class` (D211) are incompatible.",
            "warning: `multi-line-summary-first-line` (D212) and "
            "`multi-line-summary-second-line` (D213) are incompatible.",
        ]
    else:
        config = (
            '[tool.ruff.lint]\nselect = ["F401", "F821"]\n'
            f'ignore = ["{request.param}"]\n'
        )
        warning_fragments = [
            "warning: The following rules have been removed and ignoring them has no effect:",
            f"    - {request.param}",
        ]
    (project / "pyproject.toml").write_text(config)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("LINTRO_TEST_MODE", raising=False)
    monkeypatch.setenv("LINTRO_SKIP_CONFIG_INJECTION", "1")
    return project / "a.py", warning_fragments


@pytest.mark.parametrize(
    ("source", "codes"),
    [
        ("x = 1\n", []),
        ("import os\n", ["F401"]),
        ("import os\nprint(undefined_name)\n", ["F401", "F821"]),
    ],
)
def test_native_multiline_warning_statistics(
    native_warning_project: tuple[Path, list[str]],
    native_warning_color: str | None,
    source: str,
    codes: list[str],
) -> None:
    """Combined real output has the same findings as native JSON stdout."""
    source_path, warning_fragments = native_warning_project
    source_path.write_text(source)
    tool = RuffPlugin()
    process = tool._run_subprocess_result(
        build_ruff_check_command(tool, [str(source_path)]),
    )

    assert_that(process.returncode).is_equal_to(int(bool(codes)))
    assert_that(strip_ansi_codes(process.stderr)).contains(*warning_fragments)
    assert_that("\x1b[" in process.stderr).is_equal_to(native_warning_color is not None)
    if native_warning_color and "    - " in process.stderr:
        assert_that(process.stderr.splitlines()).contains("\x1b[0m")
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
    native_warning_project: tuple[Path, list[str]],
    unsafe_fixes: bool,
    source: str,
    expected_source: str,
    initial_codes: list[str],
    remaining_codes: list[str],
) -> None:
    """Actual fix keeps the F821 residual after removing F401, including its probe."""
    source_path, _warning_fragments = native_warning_project
    source_path.write_text(source)
    tool = RuffPlugin()
    checked = tool.check([str(source_path)], {"format_check": False})
    assert_that(checked.parse_failures_count).is_zero()
    assert_that(checked.findings_complete).is_true()
    assert_that(checked.issues).extracting("code").is_equal_to(initial_codes)

    result = tool.fix(
        [str(source_path)],
        {"format": False, "unsafe_fixes": unsafe_fixes},
    )

    assert_that(source_path.read_text()).is_equal_to(expected_source)
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
